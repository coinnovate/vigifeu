"""SEO/GEO au niveau du site (Spec 04 §5-6) : sitemaps, robots, llms.txt, Atom, 301.

Artefacts « site-level » régénérés en fin de build (passe nocturne, Spec 04 §3), pas
par page. `lastmod`/`updated` = horodatage de la **donnée** la plus récente (jamais
l'heure de génération, §9.5).
"""

from __future__ import annotations

import sqlite3
from html import escape
from pathlib import Path

from vigifeu.generate.jsonld import _to_iso
from vigifeu.generate.writer import write_atomic

# Crawlers IA explicitement autorisés (Spec 04 §6) — la visibilité dans les assistants
# est un canal d'acquisition, pas une fuite : le socle public est fait pour être cité.
CRAWLERS_IA = ["GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended", "CCBot", "cohere-ai"]


def _lieu(public_id: str) -> str:
    return public_id.partition("-")[2].replace("-", " ").title()


def _urlset(urls: list[tuple[str, str | None]]) -> str:
    lignes = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod in urls:
        m = f"<lastmod>{lastmod[:10]}</lastmod>" if lastmod else ""
        lignes.append(f"<url><loc>{escape(loc)}</loc>{m}</url>")
    lignes.append("</urlset>")
    return "\n".join(lignes) + "\n"


def build_sitemaps(conn: sqlite3.Connection, config: dict) -> dict:
    gen = config["generate"]
    base, site = gen["base_url"], Path(gen["site_dir"])

    feux = [(f"{base}/feux/{r['public_id']}/", r["last_acq_at"])
            for r in conn.execute(
                "SELECT public_id, last_acq_at FROM fire_event "
                "WHERE public_id IS NOT NULL AND merged_into IS NULL "
                "ORDER BY last_acq_at DESC")]

    # Périmètre commune indexable = communes concernées OU à historique (cadrage §8.6).
    communes = [(f"{base}/communes/{r['code_insee']}-{r['slug']}/", None)
                for r in conn.execute(
                    "SELECT DISTINCT c.code_insee, c.slug FROM commune c "
                    "WHERE c.code_insee IN (SELECT code_insee FROM fe_commune_rel) "
                    "   OR c.code_insee IN (SELECT code_insee FROM commune_fire_history) "
                    "ORDER BY c.code_insee")]

    pages = [(f"{base}/", None), (f"{base}/methodologie/", None),
             (f"{base}/mentions-legales/", None), (f"{base}/cgu/", None)]

    write_atomic(site / "sitemap-feux.xml", _urlset(feux))
    write_atomic(site / "sitemap-communes.xml", _urlset(communes))
    write_atomic(site / "sitemap-pages.xml", _urlset(pages))

    index = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for name in ("sitemap-feux.xml", "sitemap-communes.xml", "sitemap-pages.xml"):
        index.append(f"<sitemap><loc>{base}/{name}</loc></sitemap>")
    index.append("</sitemapindex>")
    write_atomic(site / "sitemap.xml", "\n".join(index) + "\n")
    return {"feux": len(feux), "communes": len(communes), "pages": len(pages)}


def write_robots(config: dict) -> Path:
    gen = config["generate"]
    lignes = ["User-agent: *", "Allow: /", ""]
    for bot in CRAWLERS_IA:
        lignes += [f"User-agent: {bot}", "Allow: /", ""]
    lignes.append(f"Sitemap: {gen['base_url']}/sitemap.xml")
    return write_atomic(Path(gen["site_dir"]) / "robots.txt", "\n".join(lignes) + "\n")


def write_llms(config: dict) -> Path:
    gen = config["generate"]
    m = gen["marque"]
    txt = f"""# {m}

> Veille satellitaire des incendies de végétation en France. Faits datés, sourcés et
> stables, faits pour être cités.

{m} publie, pour chaque feu suivi et chaque commune, des énoncés dérivés directement des
données (détections satellitaires NASA FIRMS, référentiels IGN, historique BDIFF), avec
leur horodatage en UTC et leur source.

## Sémantique des libellés
- « plus détecté » : aucun hotspot depuis plusieurs heures — n'est PAS « éteint ».
- « aucune détection au dernier passage » : inférence, pas une observation directe.
- « emprise estimée » : estimation satellite non officielle.
- {m} n'emploie jamais « menacé », « propagation estimée », « sera touché ».

## Sources
- Détections : NASA FIRMS / LANCE / ESDIS (VIIRS).
- Limites administratives : IGN Admin Express. Météo : Open-Meteo.
- Historique : BDIFF. Restrictions d'eau : VigiEau. Danger : Copernicus/EFFIS, Météo-France.

## Citation
Citer avec la date d'observation (UTC) et le lien de la page.
"""
    return write_atomic(Path(gen["site_dir"]) / "llms.txt", txt)


def build_atom(conn: sqlite3.Connection, config: dict) -> Path:
    gen = config["generate"]
    base = gen["base_url"]
    maj = conn.execute("SELECT MAX(acq_at) AS m FROM hotspot_raw").fetchone()["m"]
    updated = _to_iso(maj) or _to_iso("1970-01-01T00:00:00Z")
    lignes = ['<?xml version="1.0" encoding="utf-8"?>',
              '<feed xmlns="http://www.w3.org/2005/Atom">',
              f"<title>{escape(gen['marque'])} — feux publiés</title>",
              f'<link href="{base}/feux.xml" rel="self"/>',
              f'<link href="{base}/"/>',
              f"<id>{base}/feux.xml</id>",
              f"<updated>{updated}</updated>"]
    for r in conn.execute(
        "SELECT public_id, first_acq_at, last_acq_at FROM fire_event "
        "WHERE public_id IS NOT NULL AND merged_into IS NULL "
        "ORDER BY last_acq_at DESC LIMIT 50"
    ):
        url = f"{base}/feux/{r['public_id']}/"
        lignes += [
            "<entry>",
            f"<title>Feu de {escape(_lieu(r['public_id']))}</title>",
            f'<link href="{url}"/>',
            f"<id>{url}</id>",
            f"<updated>{_to_iso(r['last_acq_at'])}</updated>",
            f"<published>{_to_iso(r['first_acq_at'])}</published>",
            "</entry>",
        ]
    lignes.append("</feed>")
    return write_atomic(Path(gen["site_dir"]) / "feux.xml", "\n".join(lignes) + "\n")


def build_redirects(conn: sqlite3.Connection, config: dict) -> int:
    """Redirections 301 des feux fusionnés → feu absorbant (Spec 04 §4). Format map Nginx.

    Une URL publiée ne meurt jamais (Spec 01 P6) : un feu absorbé garde son URL, qui
    redirige (301) vers le feu absorbant.
    """
    gen = config["generate"]
    rows = conn.execute(
        "SELECT a.public_id AS src, b.public_id AS dst "
        "FROM fire_event a JOIN fire_event b ON b.id = a.merged_into "
        "WHERE a.public_id IS NOT NULL AND b.public_id IS NOT NULL"
    ).fetchall()
    lignes = ["# Redirections 301 — feux fusionnés (généré, Spec 04 §4). À inclure dans Nginx.",
              "# map $uri $redirect_target { ... }"]
    for r in rows:
        lignes.append(f"/feux/{r['src']}/ /feux/{r['dst']}/;")
    write_atomic(Path(gen["site_dir"]) / "redirects.map", "\n".join(lignes) + "\n")
    return len(rows)
