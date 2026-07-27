"""Tests de la migration 002 — schéma intégral Spec 01 (Lot 1, étape 1.0).

Vérifie que le schéma complet est en place et porte les principes structurants :
tables présentes, vocabulaires contrôlés (P4), immuabilité honorée par le schéma,
relations d'historisation (fe_commune_rel fermée jamais supprimée).
"""

from __future__ import annotations

import sqlite3

import pytest

from vigifeu.model.db import connect, migrate

# Toutes les tables attendues après migration 002 (Spec 01 §2).
TABLES_ATTENDUES = {
    # migration 001
    "schema_version", "satellite_source", "ingestion_run", "hotspot_raw",
    # référentiels (§5)
    "commune", "commune_succession", "commune_fire_history",
    # interprétation (§4)
    "fire_event", "fire_event_version", "fe_hotspot", "fire_cell_state",
    "fixed_source", "fe_fe_rel", "fe_commune_rel",
    # observations brutes (§3)
    "overpass", "weather_obs", "weather_forecast", "drought_obs", "vigieau_arrete",
    # techniques (§3.8)
    "geo_detection_raw", "regen_queue",
}


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


def _tables(c: sqlite3.Connection) -> set[str]:
    return {
        r["name"]
        for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _colonnes(c: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def test_version_schema(conn):
    """La migration 002 (et les suivantes) sont appliquées."""
    v = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"]
    assert v >= 2


def test_toutes_les_tables_presentes(conn):
    assert TABLES_ATTENDUES <= _tables(conn)


def test_hotspot_raw_intact(conn):
    """La migration 002 ne touche pas hotspot_raw (immuable, déjà peuplée en prod)."""
    cols = _colonnes(conn, "hotspot_raw")
    assert {"acq_at", "ingested_at", "overpass_id", "fixed_source_id"} <= cols


def test_fire_event_colonnes(conn):
    cols = _colonnes(conn, "fire_event")
    attendu = {
        "id", "public_id", "created_at", "first_acq_at", "last_acq_at",
        "qualification", "qualification_reason", "lifecycle", "merged_into",
        "confidence_level",
    }
    assert attendu <= cols


def test_area_estimee_suffixe(conn):
    """Spec 01 §7 : les champs estimee portent le suffixe _estimee (P4, impossible à afficher par erreur)."""
    assert "area_ha_estimee" in _colonnes(conn, "fire_event_version")


def test_public_id_unique_mais_nullable(conn):
    """public_id : UNIQUE (URL pérenne P6) mais NULL tant que non publié (suspects)."""
    now = "2026-07-22T12:00:00Z"
    conn.execute("INSERT INTO fire_event (created_at) VALUES (?)", (now,))
    conn.execute("INSERT INTO fire_event (created_at) VALUES (?)", (now,))
    conn.commit()
    # Deux feux sans public_id coexistent (plusieurs NULL autorisés).
    n = conn.execute("SELECT COUNT(*) AS n FROM fire_event WHERE public_id IS NULL").fetchone()["n"]
    assert n == 2
    # Mais deux public_id identiques sont refusés.
    conn.execute("UPDATE fire_event SET public_id='2026-saumos' WHERE id=1")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE fire_event SET public_id='2026-saumos' WHERE id=2")


def test_check_lifecycle(conn):
    """Vocabulaire contrôlé (P4) : un lifecycle hors taxonomie est rejeté."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO fire_event (created_at, lifecycle) VALUES (?, 'eteint')",
            ("2026-07-22T12:00:00Z",),
        )


def test_check_qualification(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO fire_event (created_at, qualification) VALUES (?, 'inconnu')",
            ("2026-07-22T12:00:00Z",),
        )


def test_check_indicator_drought(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO drought_obs (indicator, dept, valid_date) VALUES ('xyz', '33', '2026-07-22')"
        )


def test_check_niveau_vigieau(conn):
    conn.execute("INSERT INTO commune (code_insee, slug, nom) VALUES ('33333', 'le-porge', 'Le Porge')")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO vigieau_arrete (code_insee, niveau, date_debut) "
            "VALUES ('33333', 'panique', '2026-07-22')"
        )


def test_weather_forecast_cible_obligatoire(conn):
    """Une prévision doit viser un feu OU une commune (CHECK)."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO weather_forecast (model_run_at, valid_at, fetched_at) "
            "VALUES ('2026-07-22T00:00:00Z', '2026-07-22T12:00:00Z', '2026-07-22T00:05:00Z')"
        )


def test_foreign_keys_actives(conn):
    """Un fire_event_version orphelin (fire_event inexistant) est refusé."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO fire_event_version (fire_event_id, version_n, computed_at) "
            "VALUES (999, 1, '2026-07-22T12:00:00Z')"
        )


def test_fe_commune_rel_historisee(conn):
    """Une relation fermée (valid_to renseigné) reste en base : historique, jamais supprimée (§5.4)."""
    conn.execute("INSERT INTO commune (code_insee, slug, nom) VALUES ('33333', 'le-porge', 'Le Porge')")
    conn.execute("INSERT INTO fire_event (created_at) VALUES ('2026-07-22T12:00:00Z')")
    conn.execute(
        "INSERT INTO fe_commune_rel (fire_event_id, code_insee, rel_type, valid_from, valid_to) "
        "VALUES (1, '33333', 'emprise_dans_commune', '2026-07-22T14:32:00Z', '2026-07-25T08:00:00Z')"
    )
    conn.commit()
    r = conn.execute("SELECT valid_from, valid_to FROM fe_commune_rel").fetchone()
    assert r["valid_from"] == "2026-07-22T14:32:00Z"
    assert r["valid_to"] == "2026-07-25T08:00:00Z"


def test_migration_idempotente(conn, tmp_path):
    """Relancer migrate() sur une base déjà à jour n'applique rien (runner)."""
    applied = migrate(conn)  # déjà migrée par la fixture
    assert applied == []
