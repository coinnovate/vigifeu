"""Consommateur de `regen_queue` (Spec 04 §2, §3).

Premier et unique consommateur de la file alimentée par le pipeline (Spec 02 §8).
Lit les pages en attente (`processed_at IS NULL`), les régénère par type, écrit
chaque page par renommage atomique (P5), puis marque la ligne traitée. Ne régénère
jamais « tout le site » (P2) : seulement ce que le pipeline a signalé impacté.

Étape B : seul le type `feu` est pris en charge. `commune` et `carte` (étape C)
restent en file — non marqués, donc repris au prochain passage une fois câblés.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

from jinja2 import Environment

from vigifeu.generate.feu import load_fire_context, render_feu
from vigifeu.generate.publish import ensure_public_id
from vigifeu.generate.templating import make_env
from vigifeu.generate.writer import page_path, write_atomic


def _handle_feu(conn, config, env, page_ref, site_dir) -> Path | None:
    """Génère la fiche d'un feu. None si le feu n'est pas publiable (suspect)."""
    event_id = int(page_ref)
    public_id = ensure_public_id(conn, event_id)
    if public_id is None:
        return None
    ctx = load_fire_context(conn, config, event_id)
    html = render_feu(env, ctx)
    return write_atomic(page_path(site_dir, "feu", public_id), html)


_HANDLERS = {
    "feu": _handle_feu,
    # "commune": _handle_commune,   # étape C
    # "carte": _handle_carte,       # étape C
}


def sync_static(config: dict) -> None:
    """Copie les assets statiques (css, js, pmtiles) dans le site généré."""
    src = Path(config["generate"]["static_dir"])
    if not src.exists():
        return
    dst = Path(config["generate"]["site_dir"]) / "static"
    shutil.copytree(src, dst, dirs_exist_ok=True)


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
