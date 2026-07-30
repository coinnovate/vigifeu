"""Pages statiques éditoriales (Spec 04 §4) : méthodologie, mentions légales, CGU, 404.

Ces pages ne dépendent pas des données (générées une fois par build, hors regen_queue).
La méthodologie est un **signal de fiabilité** (cadrage §15bis, Spec 04 §6) : sources
nommées, latence chiffrée, définitions des libellés en FAQPage (JSON-LD).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from jinja2 import Environment

from vigifeu.generate import jsonld, og
from vigifeu.generate.writer import write_atomic
from vigifeu.lexique import fr


def latence_nrt_stats(conn: sqlite3.Connection, config: dict) -> dict | None:
    """Latence NRT mesurée (ingested_at − acq_at) : médiane, 90ᵉ centile, effectif (minutes).

    Argument chiffré de la page méthodologie (cadrage §15bis, plan §5.2). **Filtre
    anti-backfill** : on exclut les détections dont la latence dépasse `latence_nrt_max_h` —
    les jours re-téléchargés (backfill/récupération) ont un `ingested_at` tardif de plusieurs
    jours qui fausserait la mesure, alors que la latence NRT ne se rejoue pas (P5). None tant
    qu'il n'y a pas assez de mesures temps réel pour un chiffre crédible.
    """
    max_min = config["generate"].get("latence_nrt_max_h", 6) * 60
    lat: list[float] = []
    for r in conn.execute("SELECT acq_at, ingested_at FROM hotspot_raw WHERE ingested_at IS NOT NULL"):
        try:
            a = datetime.fromisoformat(r["acq_at"].replace("Z", "+00:00"))
            i = datetime.fromisoformat(r["ingested_at"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        minutes = (i - a).total_seconds() / 60
        if 0 <= minutes <= max_min:
            lat.append(minutes)
    if len(lat) < 50:
        return None
    lat.sort()
    n = len(lat)
    return {"n": fr.nombre_fr(n), "mediane_min": round(lat[n // 2]),
            "p90_min": round(lat[min(n - 1, int(n * 0.9))])}

# Définitions des libellés — reprises telles quelles dans la FAQ visible ET le JSON-LD.
FAQ_LIBELLES: list[tuple[str, str]] = [
    ("Que signifie « plus détecté » ?",
     "Qu'aucun point chaud n'a été détecté depuis plusieurs heures. Ce n'est pas « éteint » : "
     "un feu peut ne plus émettre de signal thermique détectable tout en restant actif au sol. "
     "Seules les autorités qualifient un feu de maîtrisé ou éteint."),
    ("Que signifie « aucune détection au dernier passage » ?",
     "Que le dernier passage satellite n'a pas détecté ce feu, alors que d'autres détections "
     "ont eu lieu dans la même fenêtre. C'est une inférence : en bord de fauchée ou sous les "
     "nuages, « pas de détection » peut signifier « pas d'observation »."),
    ("Que signifie « emprise estimée » ?",
     "Une estimation de surface calculée à partir des seules détections satellitaires "
     "(enveloppe des points), non officielle. La surface parcourue officielle, lorsqu'elle "
     "existe, est annoncée par les autorités et citée comme telle."),
    ("Quel est le délai des détections ?",
     "Les détections satellitaires parviennent avec un délai de traitement de 1 à 3 h après "
     "le passage. Un départ de feu peut précéder de plusieurs heures sa première détection."),
]


def _base_ctx(config: dict, *, slug: str, title: str, description: str, graph) -> dict:
    gen = config["generate"]
    return {
        "base_url": gen["base_url"],
        "marque": gen["marque"],
        "canonical_path": f"/{slug}/",
        "og_image": og.og_path(None),
        "jsonld": graph,
        "page_title": f"{title} | {gen['marque']}",
        "page_description": description,
        "fil_ariane": [{"label": "Accueil", "href": "/"}, {"label": title, "href": None}],
        "latence_texte": None,
        "attributions": [],
    }


def build_static_pages(conn: sqlite3.Connection, config: dict, env: Environment) -> int:
    """Génère les pages éditoriales et la 404. Retourne le nombre de pages écrites."""
    gen = config["generate"]
    site = Path(gen["site_dir"])
    org = jsonld.organization(gen["base_url"], gen["marque"])
    n = 0

    faq = jsonld.faq_page(gen["base_url"], "/methodologie/", FAQ_LIBELLES)
    ctx = _base_ctx(config, slug="methodologie", title="Méthodologie",
                    description="Sources, latence des détections satellitaires et définitions des libellés.",
                    graph=jsonld.render_graph(org, faq))
    ctx["faq"] = FAQ_LIBELLES
    ctx["latence_nrt"] = latence_nrt_stats(conn, config)   # mesure chiffrée (cadrage §15bis)
    write_atomic(site / "methodologie" / "index.html", env.get_template("methodologie.html.j2").render(**ctx))
    n += 1

    ctx = _base_ctx(config, slug="mentions-legales", title="Mentions légales",
                    description="Éditeur, hébergeur et sources de données du site.",
                    graph=jsonld.render_graph(org))
    ctx["legal"] = config.get("legal", {})
    write_atomic(site / "mentions-legales" / "index.html", env.get_template("mentions.html.j2").render(**ctx))
    n += 1

    ctx = _base_ctx(config, slug="cgu", title="Conditions d'utilisation",
                    description="Un outil de veille, pas un système d'alerte.",
                    graph=jsonld.render_graph(org))
    write_atomic(site / "cgu" / "index.html", env.get_template("cgu.html.j2").render(**ctx))
    n += 1

    # 404 à la racine (Nginx : error_page 404 /404.html)
    ctx = _base_ctx(config, slug="404", title="Page introuvable",
                    description="Cette page n'existe pas ou plus.", graph=jsonld.render_graph(org))
    ctx["canonical_path"] = "/404.html"
    write_atomic(site / "404.html", env.get_template("404.html.j2").render(**ctx))
    n += 1
    return n
