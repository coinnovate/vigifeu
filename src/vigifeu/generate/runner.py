"""Consommateur de `regen_queue` (Spec 04 §2, §3).

Premier et unique consommateur de la file alimentée par le pipeline (Spec 02 §8).
Lit les pages en attente (`processed_at IS NULL`), les régénère par type, écrit
chaque page par renommage atomique (P5), puis marque la ligne traitée. Ne régénère
jamais « tout le site » (P2) : seulement ce que le pipeline a signalé impacté.

Étape B : seul le type `feu` est pris en charge. `commune` et `carte` (étape C)
restent en file — non marqués, donc repris au prochain passage une fois câblés.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from jinja2 import Environment

from vigifeu.generate.carte import load_carte_context, render_carte
from vigifeu.generate.commune import load_commune_context, render_commune
from vigifeu.generate.feu import load_fire_context, render_feu
from vigifeu.generate.departement import build_departements
from vigifeu.generate.geojson import feu_geojson, national_geojson
from vigifeu.generate.og import write_og_images
from vigifeu.generate.pages import build_static_pages
from vigifeu.generate.publish import ensure_public_id
from vigifeu.generate.sitemaps import (
    build_atom,
    build_redirects,
    build_sitemaps,
    write_llms,
    write_robots,
)
from vigifeu.generate.templating import make_env
from vigifeu.generate.writer import page_path, write_atomic


def _handle_feu(conn, config, env, page_ref, site_dir) -> Path | None:
    """Génère la fiche d'un feu + son GeoJSON. None si le feu n'est pas publiable (suspect)."""
    event_id = int(page_ref)
    public_id = ensure_public_id(conn, event_id)
    if public_id is None:
        return None
    ctx = load_fire_context(conn, config, event_id)
    path = write_atomic(page_path(site_dir, "feu", public_id), render_feu(env, ctx))
    gj = json.dumps(feu_geojson(conn, config, event_id), ensure_ascii=False)
    write_atomic(path.parent / "feu.geojson", gj)
    return path


def _handle_commune(conn, config, env, page_ref, site_dir) -> Path | None:
    """Génère la fiche d'une commune. `page_ref` = code INSEE ; l'URL porte code-slug."""
    row = conn.execute("SELECT slug FROM commune WHERE code_insee=?", (page_ref,)).fetchone()
    if row is None:
        return None
    ctx = load_commune_context(conn, config, page_ref)
    html = render_commune(env, ctx)
    return write_atomic(page_path(site_dir, "commune", f"{page_ref}-{row['slug']}"), html)


def write_carte(conn, config, env, site_dir) -> Path:
    """Écrit la carte nationale (accueil `index.html`) + son GeoJSON `/feux.geojson`.

    Fonction pure de l'état courant (liste des feux publiés non archivés). Appelée par le
    handler de file (mise à jour événementielle) ET par `finalize_site` (rafraîchissement
    systématique) : la page d'accueil étant un agrégat, elle ne doit jamais rester périmée
    quand la file ne la ré-enfile pas (déploiement, passage d'un feu en archive)."""
    ctx = load_carte_context(conn, config)
    path = write_atomic(page_path(site_dir, "carte", "carte"), render_carte(env, ctx))
    gj = json.dumps(national_geojson(conn, config), ensure_ascii=False)
    write_atomic(Path(site_dir) / "feux.geojson", gj)
    return path


def _handle_carte(conn, config, env, page_ref, site_dir) -> Path:
    """Handler de file pour le type `carte` (mise à jour événementielle)."""
    return write_carte(conn, config, env, site_dir)


_HANDLERS = {
    "feu": _handle_feu,
    "commune": _handle_commune,
    "carte": _handle_carte,
}


def sync_static(config: dict) -> None:
    """Copie les assets statiques (css, js, pmtiles) puis écrit la config carte (clé env)."""
    src = Path(config["generate"]["static_dir"])
    site = Path(config["generate"]["site_dir"])
    if src.exists():
        shutil.copytree(src, site / "static", dirs_exist_ok=True)
    write_carte_config(config)
    write_og_images(config)


def finalize_site(conn: sqlite3.Connection, config: dict, env: Environment | None = None) -> dict:
    """Artefacts « site-level » (Spec 04 §3, passe nocturne) : pages éditoriales, sitemaps,
    robots, llms.txt, flux Atom, redirections 301. Régénérés en fin de build."""
    env = env or make_env(config["generate"]["templates_dir"], analytics=config.get("analytics"))
    # La carte (accueil) est un agrégat : on la régénère systématiquement ici pour qu'elle
    # ne reste jamais périmée quand la file ne la ré-enfile pas (déploiement, archivage).
    write_carte(conn, config, env, config["generate"]["site_dir"])
    stats = {
        "pages": build_static_pages(conn, config, env),
        "departements": build_departements(conn, config, env),
        "sitemaps": build_sitemaps(conn, config),
        "redirects": build_redirects(conn, config),
    }
    write_robots(config)
    write_llms(config)
    build_atom(conn, config)
    return stats


def write_carte_config(config: dict) -> Path:
    """Écrit /static/carte-config.js avec la clé MapTiler (secret d'env, jamais en dur).

    La clé n'entre JAMAIS dans le HTML des fiches ni le dépôt : elle vit uniquement
    dans ce fichier généré (sous data/site, non versionné). Sans clé, la carte se
    masque proprement et l'alternative textuelle reste (Spec 04 §8).
    """
    key = os.environ.get("VIGIFEU_MAPTILER_KEY", "")
    mapid = config["generate"].get("maptiler_map", "dataviz")
    # Config imagerie Sentinel-2 via CDSE Sentinel Hub (Spec 06 §5, cran 2). L'ID d'instance
    # est SEMI-PUBLIC (comme la clé MapTiler) : il vient de l'env, jamais du dépôt. Sans instance,
    # `instance` reste vide → carte.js n'ajoute pas l'imagerie (dégradé, toggle masqué).
    img = config.get("imagerie", {})
    sh = {
        "wms": img.get("sentinelhub_wms", ""),
        "wfs": img.get("sentinelhub_wfs", ""),
        "typename": img.get("sentinelhub_typename", "DSS2"),
        "layer": img.get("sentinelhub_layer", ""),
        "instance": os.environ.get("VIGIFEU_SENTINELHUB_INSTANCE", ""),
        "source": img.get("sentinelhub_source", ""),
    }
    js = (
        f"window.SENTIFEU_MAPTILER_KEY = {json.dumps(key)};\n"
        f"window.SENTIFEU_MAPTILER_MAP = {json.dumps(mapid)};\n"
        f"window.SENTIFEU_SH = {json.dumps(sh, ensure_ascii=False)};\n"
    )
    dst = Path(config["generate"]["site_dir"]) / "static" / "carte-config.js"
    return write_atomic(dst, js)


def consume(conn: sqlite3.Connection, config: dict, *, stamp: str,
            env: Environment | None = None, limit: int | None = None) -> dict:
    """Régénère les pages en attente. Retourne un décompte par type.

    `stamp` horodate `processed_at` (fourni par le scheduler/CLI). Une page dont le
    type n'est pas encore câblé reste en file (comptée `differe`). Une erreur de rendu
    est isolée (comptée `erreurs`, tracée sur stderr) et ne bloque pas le lot.
    """
    env = env or make_env(config["generate"]["templates_dir"], analytics=config.get("analytics"))
    site_dir = config["generate"]["site_dir"]
    sql = "SELECT id, page_type, page_ref FROM regen_queue WHERE processed_at IS NULL ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()

    # Pré-passe : assigner le public_id de TOUS les feux de ce lot AVANT tout rendu.
    # Sinon la carte (enfilée une seule fois, donc traitée tôt) et les fiches communes
    # sont rendues avant l'assignation paresseuse faite dans _handle_feu — elles voient
    # un public_id encore NULL et la carte, qui liste `public_id IS NOT NULL`, ressort
    # vide (« aucun feu suivi ») au premier build. ensure_public_id est idempotent.
    for row in rows:
        if row["page_type"] == "feu":
            ensure_public_id(conn, int(row["page_ref"]))
    conn.commit()

    stats = {"feu": 0, "commune": 0, "carte": 0, "differe": 0, "erreurs": 0}
    for row in rows:
        handler = _HANDLERS.get(row["page_type"])
        if handler is None:
            stats["differe"] += 1
            continue
        try:
            written = handler(conn, config, env, row["page_ref"], site_dir)
        except Exception as exc:  # noqa: BLE001 — un rendu ne doit pas tuer le lot
            stats["erreurs"] += 1
            print(f"[generer] échec {row['page_type']}:{row['page_ref']} — {exc}", file=sys.stderr)
            continue
        if written is None:
            stats["differe"] += 1
            continue
        conn.execute("UPDATE regen_queue SET processed_at=? WHERE id=?", (stamp, row["id"]))
        stats[row["page_type"]] += 1
    conn.commit()
    return stats
