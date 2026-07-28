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
from vigifeu.generate.geojson import feu_geojson, national_geojson
from vigifeu.generate.publish import ensure_public_id
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


def _handle_carte(conn, config, env, page_ref, site_dir) -> Path:
    """Génère la carte nationale (accueil) + son GeoJSON `/feux.geojson`."""
    ctx = load_carte_context(conn, config)
    path = write_atomic(page_path(site_dir, "carte", page_ref), render_carte(env, ctx))
    gj = json.dumps(national_geojson(conn, config), ensure_ascii=False)
    write_atomic(Path(site_dir) / "feux.geojson", gj)
    return path


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


def write_carte_config(config: dict) -> Path:
    """Écrit /static/carte-config.js avec la clé MapTiler (secret d'env, jamais en dur).

    La clé n'entre JAMAIS dans le HTML des fiches ni le dépôt : elle vit uniquement
    dans ce fichier généré (sous data/site, non versionné). Sans clé, la carte se
    masque proprement et l'alternative textuelle reste (Spec 04 §8).
    """
    key = os.environ.get("VIGIFEU_MAPTILER_KEY", "")
    mapid = config["generate"].get("maptiler_map", "dataviz")
    js = (
        f"window.SENTIFEU_MAPTILER_KEY = {json.dumps(key)};\n"
        f"window.SENTIFEU_MAPTILER_MAP = {json.dumps(mapid)};\n"
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
    env = env or make_env(config["generate"]["templates_dir"])
    site_dir = config["generate"]["site_dir"]
    sql = "SELECT id, page_type, page_ref FROM regen_queue WHERE processed_at IS NULL ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()

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
