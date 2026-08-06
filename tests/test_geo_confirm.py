"""Tests du rattachement MTG → feu VIIRS (Spec 07 §5, étape 5).

Scénarios sur des feux VIIRS synthétiques (fire_event + fire_cell_state) et des détections MTG :
confirmation dans la fenêtre, rejet hors rayon / hors fenêtre, early-detection (MTG avant VIIRS),
idempotence, choix du feu le plus proche, borne de récence.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vigifeu.engine.geo_confirm import confirm_detections
from vigifeu.model.db import connect, load_config, migrate

NOW = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)
ISO = "%Y-%m-%dT%H:%M:%SZ"


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture()
def config():
    return load_config("config/params.toml")


def _fire(conn, *, lat=44.70, lon=-1.00, first="2026-08-06T11:00:00Z", last="2026-08-06T11:00:00Z",
          lifecycle="actif"):
    fid = conn.execute(
        "INSERT INTO fire_event (created_at, first_acq_at, last_acq_at, lifecycle) VALUES (?,?,?,?)",
        ("2026-08-06T11:00:00Z", first, last, lifecycle),
    ).lastrowid
    conn.execute(
        "INSERT INTO fire_cell_state (fire_event_id, cell_key, lat, lon) VALUES (?,?,?,?)",
        (fid, f"c{fid}", lat, lon),
    )
    conn.commit()
    return fid


def _det(conn, *, lat, lon, acq_at):
    run = conn.execute(
        "INSERT INTO ingestion_run (source, started_at) VALUES ('mtg:0682', ?)",
        ("2026-08-06T12:00:00Z",),
    ).lastrowid
    return conn.execute(
        "INSERT INTO geo_detection_raw (provider, lat, lon, acq_at, ingested_at, ingestion_run_id) "
        "VALUES ('mtg-fci-fir', ?, ?, ?, ?, ?)",
        (lat, lon, acq_at, "2026-08-06T12:00:05Z", run),
    ).lastrowid


def _confirmed(conn, det_id):
    return conn.execute(
        "SELECT confirmed_by_fire_event_id FROM geo_detection_raw WHERE id=?", (det_id,)
    ).fetchone()["confirmed_by_fire_event_id"]


def test_confirme_proche_dans_fenetre(conn, config):
    fid = _fire(conn)
    det = _det(conn, lat=44.705, lon=-1.00, acq_at="2026-08-06T11:05:00Z")  # ~600 m, +5 min
    res = confirm_detections(conn, config, clock=NOW)
    assert res == {"n_confirmed": 1, "fires": [fid]}
    assert _confirmed(conn, det) == fid


def test_hors_rayon_non_confirme(conn, config):
    _fire(conn)
    det = _det(conn, lat=44.75, lon=-1.00, acq_at="2026-08-06T11:05:00Z")  # ~5.5 km > 3 km
    assert confirm_detections(conn, config, clock=NOW)["n_confirmed"] == 0
    assert _confirmed(conn, det) is None


def test_hors_fenetre_non_confirme(conn, config):
    _fire(conn, first="2026-08-06T11:00:00Z", last="2026-08-06T11:00:00Z")
    # détection 30 h avant le feu → hors fenêtre 24 h (mais dans l'horizon 48 h → bien balayée)
    det = _det(conn, lat=44.705, lon=-1.00, acq_at="2026-08-05T05:00:00Z")
    assert confirm_detections(conn, config, clock=NOW)["n_confirmed"] == 0
    assert _confirmed(conn, det) is None


def test_early_detection_avant_viirs(conn, config):
    """MTG voit AVANT VIIRS : détection antérieure au first_acq_at du feu, dans la fenêtre → rattachée."""
    fid = _fire(conn, first="2026-08-06T11:00:00Z", last="2026-08-06T11:30:00Z")
    det = _det(conn, lat=44.702, lon=-1.00, acq_at="2026-08-06T09:30:00Z")  # 1h30 AVANT le feu, < 24 h
    assert confirm_detections(conn, config, clock=NOW)["n_confirmed"] == 1
    assert _confirmed(conn, det) == fid


def test_idempotent(conn, config):
    fid = _fire(conn)
    det = _det(conn, lat=44.705, lon=-1.00, acq_at="2026-08-06T11:05:00Z")
    confirm_detections(conn, config, clock=NOW)
    res2 = confirm_detections(conn, config, clock=NOW)          # déjà confirmée → non re-balayée
    assert res2 == {"n_confirmed": 0, "fires": []}
    assert _confirmed(conn, det) == fid


def test_feu_le_plus_proche(conn, config):
    proche = _fire(conn, lat=44.705, lon=-1.00)
    _loin = _fire(conn, lat=44.900, lon=-1.00)
    det = _det(conn, lat=44.706, lon=-1.00, acq_at="2026-08-06T11:05:00Z")
    confirm_detections(conn, config, clock=NOW)
    assert _confirmed(conn, det) == proche


def test_borne_recence(conn, config):
    """Une détection plus vieille que 2× la fenêtre n'est plus balayée (calibration, destin 3)."""
    # feu et détection très anciens mais spatio-temporellement compatibles : exclus par l'horizon.
    _fire(conn, first="2026-08-01T00:00:00Z", last="2026-08-01T00:00:00Z", lifecycle="actif")
    det = _det(conn, lat=44.705, lon=-1.00, acq_at="2026-08-01T00:05:00Z")  # > 48 h avant NOW
    assert confirm_detections(conn, config, clock=NOW)["n_confirmed"] == 0
    assert _confirmed(conn, det) is None


def test_aucune_detection(conn, config):
    _fire(conn)
    assert confirm_detections(conn, config, clock=NOW) == {"n_confirmed": 0, "fires": []}
