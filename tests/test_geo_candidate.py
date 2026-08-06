"""Tests de l'amorçage MTG via geo_candidate (Spec 07 §4.2/§5, étape 6).

Amorçage sur persistance, seuil de slots distincts, croissance d'un candidat existant, promotion
à la confirmation VIIRS, expiration, deux amas distincts, et détachement au reset_interpretation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vigifeu.engine.geo_candidate import process_candidates
from vigifeu.model.db import connect, load_config, migrate

NOW = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture()
def config():
    return load_config("config/params.toml")


def _run(conn):
    return conn.execute(
        "INSERT INTO ingestion_run (source, started_at) VALUES ('mtg:0682', ?)",
        ("2026-08-06T12:00:00Z",),
    ).lastrowid


def _det(conn, *, lat, lon, acq_at, confirmed=None, run=None):
    run = run or _run(conn)
    return conn.execute(
        "INSERT INTO geo_detection_raw (provider, lat, lon, acq_at, ingested_at, ingestion_run_id, "
        "confirmed_by_fire_event_id) VALUES ('mtg-fci-fir', ?, ?, ?, ?, ?, ?)",
        (lat, lon, acq_at, "2026-08-06T12:00:05Z", run, confirmed),
    ).lastrowid


def _cands(conn):
    return conn.execute("SELECT * FROM geo_candidate ORDER BY id").fetchall()


# slots proches (même lieu ~ Gironde), 3 instants distincts
def _trois_slots(conn):
    _det(conn, lat=44.700, lon=-1.00, acq_at="2026-08-06T11:30:00Z")
    _det(conn, lat=44.701, lon=-1.001, acq_at="2026-08-06T11:40:00Z")
    _det(conn, lat=44.700, lon=-1.002, acq_at="2026-08-06T11:50:00Z")


def test_amorcage_sur_persistance(conn, config):
    _trois_slots(conn)
    stats = process_candidates(conn, config, clock=NOW)
    assert stats["crees"] == 1
    cands = _cands(conn)
    assert len(cands) == 1
    assert cands[0]["status"] == "en_attente"
    assert cands[0]["n_detections"] == 3
    # les 3 détections portent le geo_candidate_id
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM geo_detection_raw WHERE geo_candidate_id=?", (cands[0]["id"],)
    ).fetchone()["n"]
    assert n == 3


def test_sous_seuil_pas_de_candidat(conn, config):
    _det(conn, lat=44.700, lon=-1.00, acq_at="2026-08-06T11:30:00Z")
    _det(conn, lat=44.701, lon=-1.00, acq_at="2026-08-06T11:40:00Z")   # 2 slots < 3
    stats = process_candidates(conn, config, clock=NOW)
    assert stats["crees"] == 0 and _cands(conn) == []


def test_slots_non_distincts_ne_comptent_pas(conn, config):
    """3 pixels mais 2 instants seulement → persistance insuffisante (slots distincts)."""
    _det(conn, lat=44.700, lon=-1.00, acq_at="2026-08-06T11:30:00Z")
    _det(conn, lat=44.701, lon=-1.00, acq_at="2026-08-06T11:30:00Z")   # même slot
    _det(conn, lat=44.700, lon=-1.001, acq_at="2026-08-06T11:40:00Z")
    assert process_candidates(conn, config, clock=NOW)["crees"] == 0


def test_croissance_candidat_existant(conn, config):
    _trois_slots(conn)
    process_candidates(conn, config, clock=NOW)               # crée le candidat
    _det(conn, lat=44.702, lon=-1.00, acq_at="2026-08-06T12:00:00Z")  # nouveau slot proche
    stats = process_candidates(conn, config, clock=NOW)
    assert stats["grossis"] == 1 and stats["crees"] == 0
    assert _cands(conn)[0]["n_detections"] == 4


def test_promotion_a_la_confirmation(conn, config):
    _trois_slots(conn)
    process_candidates(conn, config, clock=NOW)
    cand = _cands(conn)[0]["id"]
    # simule geo_confirm : un feu VIIRS confirme UNE détection du candidat
    fid = conn.execute(
        "INSERT INTO fire_event (created_at, lifecycle) VALUES ('2026-08-06T12:00:00Z','actif')"
    ).lastrowid
    conn.execute(
        "UPDATE geo_detection_raw SET confirmed_by_fire_event_id=? WHERE id="
        "(SELECT id FROM geo_detection_raw WHERE geo_candidate_id=? ORDER BY id LIMIT 1)",
        (fid, cand),
    )
    conn.commit()
    stats = process_candidates(conn, config, clock=NOW)
    assert stats["promus"] == 1
    row = conn.execute("SELECT status, fire_event_id FROM geo_candidate WHERE id=?", (cand,)).fetchone()
    assert row["status"] == "confirme" and row["fire_event_id"] == fid
    # TOUTES les détections du candidat rejoignent le feu (chronologie)
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM geo_detection_raw WHERE geo_candidate_id=? AND confirmed_by_fire_event_id=?",
        (cand, fid),
    ).fetchone()["n"]
    assert n == 3


def test_expiration(conn, config):
    # candidat ancien (dernier slot il y a > t_reprise_days = 7 j) → expire
    _det(conn, lat=44.70, lon=-1.00, acq_at="2026-07-20T11:30:00Z")
    _det(conn, lat=44.70, lon=-1.001, acq_at="2026-07-20T11:40:00Z")
    _det(conn, lat=44.70, lon=-1.002, acq_at="2026-07-20T11:50:00Z")
    # amorçage à une horloge proche des détections (sinon horizon display_max_h les écarte)
    vieux = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    process_candidates(conn, config, clock=vieux)
    assert _cands(conn)[0]["status"] == "en_attente"
    stats = process_candidates(conn, config, clock=NOW)       # 17 j plus tard
    assert stats["expires"] == 1
    assert _cands(conn)[0]["status"] == "expire"


def test_deux_amas_distincts(conn, config):
    _trois_slots(conn)                                        # Gironde
    for i, t in enumerate(("11:30", "11:40", "11:50")):       # Île-de-France, loin
        _det(conn, lat=48.80 + i * 0.001, lon=2.30, acq_at=f"2026-08-06T{t}:00Z")
    stats = process_candidates(conn, config, clock=NOW)
    assert stats["crees"] == 2 and len(_cands(conn)) == 2


def test_confirmees_ne_sont_pas_amorcees(conn, config):
    """Des détections déjà confirmées (près d'un feu) ne forment pas de candidat."""
    fid = conn.execute(
        "INSERT INTO fire_event (created_at, lifecycle) VALUES ('2026-08-06T12:00:00Z','actif')"
    ).lastrowid
    _det(conn, lat=44.70, lon=-1.00, acq_at="2026-08-06T11:30:00Z", confirmed=fid)
    _det(conn, lat=44.70, lon=-1.001, acq_at="2026-08-06T11:40:00Z", confirmed=fid)
    _det(conn, lat=44.70, lon=-1.002, acq_at="2026-08-06T11:50:00Z", confirmed=fid)
    assert process_candidates(conn, config, clock=NOW)["crees"] == 0


def test_reset_interpretation_detache_candidat(tmp_path):
    """Le wipe détache geo_candidate (fire_event supprimé) sans orpheline FK (Spec 07 §5)."""
    from vigifeu.engine.pipeline import reset_interpretation

    c = connect(tmp_path / "w.db")
    migrate(c)
    cfg = load_config("config/params.toml")
    from vigifeu.model.db import sync_satellite_sources
    sync_satellite_sources(c, cfg)
    fid = c.execute(
        "INSERT INTO fire_event (created_at, lifecycle) VALUES ('2026-08-06T12:00:00Z','actif')"
    ).lastrowid
    c.execute(
        "INSERT INTO geo_candidate (created_at, first_acq_at, last_acq_at, centroid_lat, centroid_lon, "
        "n_detections, status, fire_event_id) VALUES ('x','x','x',44.7,-1.0,3,'confirme',?)", (fid,)
    )
    c.commit()
    reset_interpretation(c, cfg)   # ne doit pas lever (foreign_key_check propre)
    row = c.execute("SELECT status, fire_event_id FROM geo_candidate").fetchone()
    assert row["status"] == "en_attente" and row["fire_event_id"] is None
    c.close()
