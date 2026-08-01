"""Tests de la migration 004 — référentiel POI / enjeux (Spec 06, phase 2, bloc 1, étape 1).

Vérifie les trois tables (`poi`, `fe_poi_rel`, `commune_poi`), l'idempotence par clé
naturelle (P5), l'historisation de fe_poi_rel (jamais supprimée, fermée) et le point-dans
-polygone commune_poi. Confirme aussi que `category` / `rel_type` restent SANS CHECK
(le jeu v1 s'élargira sans migration — même décision que fe_commune_rel).
"""

from __future__ import annotations

import sqlite3

import pytest

from vigifeu.model.db import connect, migrate

NOW = "2026-08-01T12:00:00Z"


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


def _colonnes(c: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def _poi(c, source="osm", ref="node/1", category="camping", nom="Camping X",
         lat=44.9, lon=-1.1):
    c.execute(
        "INSERT INTO poi (source, source_ref, category, nom, lat, lon, imported_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (source, ref, category, nom, lat, lon, NOW),
    )
    return c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def test_version_schema(conn):
    v = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"]
    assert v >= 4


def test_tables_presentes(conn):
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"poi", "fe_poi_rel", "commune_poi"} <= tables


def test_poi_colonnes(conn):
    attendu = {"id", "source", "source_ref", "category", "nom", "lat", "lon",
               "enjeu_json", "imported_at"}
    assert attendu <= _colonnes(conn, "poi")


def test_poi_idempotence_cle_naturelle(conn):
    """UNIQUE (source, source_ref) = clé d'idempotence : ré-import sans doublon (P5)."""
    _poi(conn, source="osm", ref="node/42")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _poi(conn, source="osm", ref="node/42", nom="Doublon")
    # Même ref dans une autre source = licite (dédup inter-sources gérée par le code, pas le schéma).
    _poi(conn, source="bdtopo", ref="node/42")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS n FROM poi").fetchone()["n"] == 2


def test_poi_category_sans_check(conn):
    """category est libre : le jeu v1 s'élargira (v1.1) sans migration."""
    _poi(conn, ref="node/99", category="categorie_future_v1_1")
    conn.commit()  # ne lève pas


def test_fe_poi_rel_fk(conn):
    """Une relation vers un POI inexistant est refusée (FK active)."""
    conn.execute("INSERT INTO fire_event (created_at) VALUES (?)", (NOW,))
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO fe_poi_rel (fire_event_id, poi_id, rel_type, valid_from) "
            "VALUES (1, 999, 'emprise', ?)",
            (NOW,),
        )


def test_fe_poi_rel_historisee(conn):
    """Une relation fermée (valid_to renseigné) reste en base : historique, jamais supprimée."""
    conn.execute("INSERT INTO fire_event (created_at) VALUES (?)", (NOW,))
    pid = _poi(conn)
    conn.execute(
        "INSERT INTO fe_poi_rel (fire_event_id, poi_id, rel_type, distance_km, valid_from, valid_to) "
        "VALUES (1, ?, 'a_moins_de_5km', 2.3, ?, '2026-08-03T08:00:00Z')",
        (pid, NOW),
    )
    conn.commit()
    r = conn.execute("SELECT rel_type, distance_km, valid_to FROM fe_poi_rel").fetchone()
    assert r["rel_type"] == "a_moins_de_5km"
    assert r["distance_km"] == 2.3
    assert r["valid_to"] == "2026-08-03T08:00:00Z"


def test_commune_poi_pk_et_fk(conn):
    """commune_poi : couple unique (PK composite) + FK commune et poi."""
    conn.execute("INSERT INTO commune (code_insee, slug, nom) VALUES ('33333', 'le-porge', 'Le Porge')")
    pid = _poi(conn)
    conn.commit()
    conn.execute("INSERT INTO commune_poi (code_insee, poi_id) VALUES ('33333', ?)", (pid,))
    conn.commit()
    # Doublon du couple refusé (PK).
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO commune_poi (code_insee, poi_id) VALUES ('33333', ?)", (pid,))
    # POI inexistant refusé (FK).
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO commune_poi (code_insee, poi_id) VALUES ('33333', 999)")


def test_migration_idempotente(conn):
    assert migrate(conn) == []
