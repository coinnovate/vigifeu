"""Accès SQLite et runner de migrations.

Décisions (plan de dev §1.1) : sqlite3 standard, mode WAL, pas d'ORM,
migrations = scripts SQL numérotés + table schema_version.
Contrainte structurante : UN SEUL processus écrivain (le daemon scheduler).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tomllib
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{3})_.+\.sql$")

# Sections dont un changement doit être tracé dans les FireEvents (Spec 02 P4) :
# elles décident du clustering et de la qualification. Un ajustement de commentaire
# ou d'une section sans effet sur l'interprétation (firms, monitoring…) ne fait pas
# bouger le hash — seules les valeurs qui qualifient comptent.
_HASHED_SECTIONS = ("clustering", "qualification", "dedup", "overpass")


def load_config(path: str | Path = "config/params.toml") -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def config_hash(config: dict, sections: tuple[str, ...] = _HASHED_SECTIONS) -> str:
    """Empreinte courte et stable des paramètres d'interprétation.

    Sérialisation JSON canonique (clés triées) des seules sections décisionnelles,
    sha256 tronqué à 12 hex. Entre dans `qualification_reason` et le contexte des
    versions : chaque fiche sait avec quels paramètres elle a été produite (§5.3).
    """
    subset = {s: config.get(s, {}) for s in sections}
    blob = json.dumps(subset, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def connect(db_path: str | Path, *, cross_thread: bool = False) -> sqlite3.Connection:
    """Connexion configurée : WAL, foreign keys, timeout généreux.

    `cross_thread=False` (défaut) garde la garde stricte de sqlite3 (`check_same_thread`) :
    la connexion n'est utilisable que depuis le thread qui l'a créée. C'est ce qu'il faut
    pour la CLI et les tests, mono-thread.

    `cross_thread=True` est réservé au **daemon scheduler** : APScheduler exécute ses
    jobs planifiés dans un thread worker (pas le thread principal qui a ouvert la
    connexion), donc la garde stricte y ferait échouer toute écriture planifiée
    (`ProgrammingError`). Le daemon la relâche ET sérialise ses jobs dans un unique
    worker (`make_scheduler`, executor à 1 thread) pour préserver l'invariant « un seul
    écrivain SQLite » du plan §1.1 : relâcher la garde sans la sérialisation serait faux.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=60, check_same_thread=not cross_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return row["v"] or 0
    except sqlite3.OperationalError:
        return 0  # base vierge


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[int]:
    """Applique les migrations manquantes, dans l'ordre. Retourne les versions appliquées."""
    applied: list[int] = []
    version = current_version(conn)
    files = sorted(p for p in migrations_dir.iterdir() if _MIGRATION_RE.match(p.name))
    for path in files:
        v = int(_MIGRATION_RE.match(path.name).group(1))
        if v <= version:
            continue
        conn.executescript(path.read_text(encoding="utf-8"))
        conn.commit()
        applied.append(v)
    return applied


def sync_satellite_sources(conn: sqlite3.Connection, config: dict) -> None:
    """Peuple/actualise satellite_source depuis la config (cadrage §5.1 : jamais en dur).

    Une source retirée de la config est désactivée, jamais supprimée (ses hotspots
    la référencent).
    """
    codes_config = set()
    for src in config["firms"]["sources"]:
        codes_config.add(src["code"])
        conn.execute(
            """
            INSERT INTO satellite_source (code, platform, instrument, resolution_m, active, notes)
            VALUES (:code, :platform, :instrument, :resolution_m, :active, :notes)
            ON CONFLICT(code) DO UPDATE SET
                platform=excluded.platform, instrument=excluded.instrument,
                resolution_m=excluded.resolution_m, active=excluded.active,
                notes=excluded.notes
            """,
            {
                "code": src["code"],
                "platform": src["platform"],
                "instrument": src["instrument"],
                "resolution_m": src.get("resolution_m"),
                "active": 1 if src.get("active", True) else 0,
                "notes": src.get("notes"),
            },
        )
    conn.execute(
        f"""UPDATE satellite_source SET active=0
            WHERE code NOT IN ({",".join("?" * len(codes_config))})""",
        list(codes_config),
    )
    conn.commit()


def active_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM satellite_source WHERE active=1 ORDER BY code"
    ).fetchall()
