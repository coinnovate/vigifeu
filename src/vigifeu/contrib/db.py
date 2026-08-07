"""Accès aux bases du canal contributif (Spec 10 §2/§3).

Deux accès, deux rôles :

- **base contributions** (`connect_contrib` / `migrate_contrib`) : SÉPARÉE de la socle,
  l'API en est l'écrivain. Schéma versionné à part (`migrations_contrib/`), indépendant
  des migrations socle. Réutilise le `connect`/`migrate` du socle (mêmes conventions : WAL,
  foreign_keys, table `schema_version`) ;

- **socle en LECTURE SEULE** (`connect_socle_readonly`) : pour « feux proches » et la
  commune du hotspot. `PRAGMA query_only=ON` interdit toute écriture (lève
  `OperationalError`) ET reste compatible WAL avec le daemon écrivain — au contraire d'un
  `mode=ro` OS, fragile sur une base WAL vivante (accès au fichier -shm). L'invariant
  « un seul écrivain sur la socle = le daemon » (plan §1.1) est ainsi garanti côté API.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from vigifeu.model.db import connect, migrate

CONTRIB_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations_contrib"


def connect_contrib(db_path: str | Path, *, cross_thread: bool = False) -> sqlite3.Connection:
    """Connexion écrivain à la base contributions (WAL, FK — mêmes réglages que la socle)."""
    return connect(db_path, cross_thread=cross_thread)


def migrate_contrib(conn: sqlite3.Connection) -> list[int]:
    """Applique les migrations manquantes de la base contributions. Retourne les versions appliquées."""
    return migrate(conn, CONTRIB_MIGRATIONS_DIR)


def connect_socle_readonly(socle_path: str | Path) -> sqlite3.Connection:
    """Connexion LECTURE SEULE à la socle (feux proches, commune du hotspot).

    `query_only=ON` : toute écriture lève `sqlite3.OperationalError`. Compatible avec la
    socle en WAL écrite par le daemon (lecteur parmi N, un seul écrivain). Lève
    `FileNotFoundError` si la socle n'existe pas (jamais de création par erreur).
    """
    p = Path(socle_path)
    if not p.exists():
        raise FileNotFoundError(f"base socle introuvable : {p}")
    conn = sqlite3.connect(p, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn
