"""Tests de la migration 006 — bulletins de veille presse (Spec 09, phase 2, étape 1).

Vérifie la table `bulletin`, l'idempotence (un bulletin par feu et par jour), la FK vers
fire_event, l'immuabilité au sens du schéma (colonnes acq_at/ingested_at présentes), et que
la migration reste idempotente. Le `resume` peut être NULL/vide (feu couvert sans valeur
confirmée est géré côté job — ici on valide que le schéma l'autorise).
"""

from __future__ import annotations

import sqlite3

import pytest

from vigifeu.model.db import connect, migrate

NOW = "2026-08-04T13:00:00Z"


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


def _colonnes(c: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def _feu(c) -> int:
    c.execute("INSERT INTO fire_event (created_at) VALUES (?)", (NOW,))
    return c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _bulletin(c, fire_id, date_bulletin="2026-07-25", *, resume="Résumé.",
              mots_cles="incendie Saumos"):
    c.execute(
        "INSERT INTO bulletin (fire_event_id, date_bulletin, mots_cles, resume, "
        "provider, acq_at, ingested_at) VALUES (?, ?, ?, ?, 'co-innovate', ?, ?)",
        (fire_id, date_bulletin, mots_cles, resume, NOW, NOW),
    )


def test_version_schema(conn):
    v = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"]
    assert v >= 6


def test_table_presente(conn):
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "bulletin" in tables


def test_bulletin_colonnes(conn):
    attendu = {"id", "fire_event_id", "date_bulletin", "mots_cles", "resume",
               "indicateurs_json", "sources_json", "articles_valides", "fournisseurs_ia",
               "provider", "acq_at", "ingested_at"}
    assert attendu <= _colonnes(conn, "bulletin")


def test_idempotence_un_par_feu_et_jour(conn):
    """UNIQUE (fire_event_id, date_bulletin) : un rejeu le même jour est refusé (P1, no-op côté job)."""
    fid = _feu(conn)
    _bulletin(conn, fid, "2026-07-25")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _bulletin(conn, fid, "2026-07-25", resume="Doublon du jour")
    # Un autre jour pour le même feu = licite (timeline).
    _bulletin(conn, fid, "2026-07-26")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS n FROM bulletin").fetchone()["n"] == 2


def test_meme_jour_feux_differents_licite(conn):
    """Deux feux distincts peuvent avoir un bulletin le même jour."""
    f1, f2 = _feu(conn), _feu(conn)
    _bulletin(conn, f1, "2026-07-25")
    _bulletin(conn, f2, "2026-07-25")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS n FROM bulletin").fetchone()["n"] == 2


def test_fk_fire_event(conn):
    """Un bulletin vers un feu inexistant est refusé (FK active)."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO bulletin (fire_event_id, date_bulletin, mots_cles, provider, "
            "acq_at, ingested_at) VALUES (999, '2026-07-25', 'x', 'co-innovate', ?, ?)",
            (NOW, NOW),
        )


def test_resume_nullable(conn):
    """Le schéma autorise un `resume` vide (feu couvert sans valeur confirmée — géré côté job)."""
    fid = _feu(conn)
    _bulletin(conn, fid, resume=None)
    conn.commit()
    assert conn.execute("SELECT resume FROM bulletin").fetchone()["resume"] is None


def test_migration_idempotente(conn):
    assert migrate(conn) == []
