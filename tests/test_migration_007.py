"""Tests de la migration 007 — détection géostationnaire MTG (Spec 07, phase 2, étape 1).

Vérifie les tables `geo_candidate` et `geo_detection_raw`, l'idempotence d'ingestion
(UNIQUE provider/acq_at/lat/lon), les FK (geo_detection_raw → geo_candidate, fire_event,
ingestion_run ; geo_candidate → fire_event), la contrainte de statut du candidat, la vue
de latence NRT, la présence du paramètre de rétention en config, et que la migration reste
idempotente. On valide AUSSI l'étanchéité : geo_detection_raw ne référence PAS satellite_source
(flux séparé du socle VIIRS) et [mtg] n'entre pas dans le hash de config.
"""

from __future__ import annotations

import sqlite3

import pytest

from vigifeu.model.db import config_hash, connect, load_config, migrate

NOW = "2026-08-06T13:00:00Z"
ACQ = "2026-08-06T12:40:00Z"


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


def _run(c) -> int:
    c.execute(
        "INSERT INTO ingestion_run (source, started_at) VALUES ('mtg:0682', ?)", (NOW,)
    )
    return c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _candidat(c, *, status="en_attente") -> int:
    c.execute(
        "INSERT INTO geo_candidate (created_at, first_acq_at, last_acq_at, centroid_lat, "
        "centroid_lon, n_detections, status) VALUES (?, ?, ?, 44.7, -1.0, 3, ?)",
        (NOW, ACQ, ACQ, status),
    )
    return c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _detection(c, run_id, *, lat=44.7, lon=-1.0, acq_at=ACQ, candidate_id=None, fire_id=None):
    c.execute(
        "INSERT INTO geo_detection_raw (provider, lat, lon, acq_at, ingested_at, "
        "ingestion_run_id, frp_mw, confidence, geo_candidate_id, confirmed_by_fire_event_id) "
        "VALUES ('mtg-fci-fir', ?, ?, ?, ?, ?, 12.5, 'nominal', ?, ?)",
        (lat, lon, acq_at, NOW, run_id, candidate_id, fire_id),
    )


def test_version_schema(conn):
    v = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"]
    assert v >= 7


def test_tables_presentes(conn):
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"geo_candidate", "geo_detection_raw"} <= tables


def test_geo_detection_colonnes(conn):
    attendu = {"id", "provider", "lat", "lon", "acq_at", "ingested_at", "ingestion_run_id",
               "frp_mw", "frp_uncertainty_mw", "confidence", "quality_flag",
               "geo_candidate_id", "confirmed_by_fire_event_id", "raw_payload"}
    assert attendu <= _colonnes(conn, "geo_detection_raw")


def test_geo_candidate_colonnes(conn):
    attendu = {"id", "created_at", "first_acq_at", "last_acq_at", "centroid_lat",
               "centroid_lon", "n_detections", "status", "fire_event_id"}
    assert attendu <= _colonnes(conn, "geo_candidate")


def test_etancheite_pas_de_source_id(conn):
    """geo_detection_raw est un flux SÉPARÉ : pas de FK satellite_source (≠ hotspot_raw)."""
    assert "source_id" not in _colonnes(conn, "geo_detection_raw")


def test_idempotence_ingestion(conn):
    """UNIQUE (provider, acq_at, lat, lon) : réingérer un slot connu est refusé (no-op côté fetcher)."""
    run_id = _run(conn)
    _detection(conn, run_id)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _detection(conn, run_id)
    # Même position, autre slot = licite (le film 10 min).
    _detection(conn, run_id, acq_at="2026-08-06T12:50:00Z")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS n FROM geo_detection_raw").fetchone()["n"] == 2


def test_fk_ingestion_run(conn):
    """Une détection vers un run inexistant est refusée (FK active)."""
    with pytest.raises(sqlite3.IntegrityError):
        _detection(conn, 999)


def test_fk_candidate_et_fire_event(conn):
    """Les liaisons vers geo_candidate et fire_event fonctionnent et sont contraintes."""
    run_id = _run(conn)
    cand = _candidat(conn)
    feu = _feu(conn)
    _detection(conn, run_id, candidate_id=cand, fire_id=feu)
    conn.commit()
    row = conn.execute(
        "SELECT geo_candidate_id, confirmed_by_fire_event_id FROM geo_detection_raw"
    ).fetchone()
    assert row["geo_candidate_id"] == cand
    assert row["confirmed_by_fire_event_id"] == feu
    # candidat inexistant → refusé
    with pytest.raises(sqlite3.IntegrityError):
        _detection(conn, run_id, lat=45.0, candidate_id=999)


def test_candidate_statut_contraint(conn):
    """Le CHECK sur status refuse une valeur hors énumération."""
    _candidat(conn, status="confirme")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _candidat(conn, status="n_importe_quoi")


def test_vue_latence_nrt_mtg(conn):
    """La vue calcule ingested_at − acq_at en heures (20 min → 0.33 h)."""
    run_id = _run(conn)
    _detection(conn, run_id)  # acq 12:40, ingested 13:00 → 20 min
    conn.commit()
    lat = conn.execute("SELECT latence_h, source FROM v_latence_nrt_mtg").fetchone()
    assert lat["source"] == "mtg-fci-fir"
    assert lat["latence_h"] == pytest.approx(0.33, abs=0.01)


def test_retention_config_presente(conn):
    """Le paramètre de rétention MTG existe dans la config versionnée (Spec 07 §4.3)."""
    cfg = load_config()
    assert "geo_detection_retention_days" in cfg["archive"]
    assert "mtg" in cfg
    assert cfg["mtg"]["collection_id"] == "EO:EUM:DAT:0682"
    assert isinstance(cfg["mtg"]["activated"], bool)   # présent (activé en prod le 2026-08-06)


def test_mtg_hors_hash_config():
    """[mtg] ne décide ni du clustering ni de la qualification → n'entre pas dans config_hash (étanchéité)."""
    cfg = load_config()
    h_avant = config_hash(cfg)
    cfg["mtg"]["seed_min_detections"] = 999
    assert config_hash(cfg) == h_avant


def test_migration_idempotente(conn):
    assert migrate(conn) == []
