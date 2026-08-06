"""Calque « signaux géostationnaires en attente » (Spec 07 §8/§8bis, étape 8).

Couvre : persistance (min slots), masquage des sources fixes confirmées, exclusion des détections
confirmées et anciennes, un vrai amas près (mais hors rayon) d'une industrie reste affiché, et la
forme des features (carré, libellé imposé, jamais de public_id).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vigifeu.generate.geojson import geo_signals_geojson
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


def _det(conn, *, lat, lon, acq_at, confirmed=None):
    run = conn.execute(
        "INSERT INTO ingestion_run (source, started_at) VALUES ('mtg:0682','2026-08-06T12:00:00Z')"
    ).lastrowid
    conn.execute(
        "INSERT INTO geo_detection_raw (provider, lat, lon, acq_at, ingested_at, ingestion_run_id, "
        "confirmed_by_fire_event_id) VALUES ('mtg-fci-fir', ?, ?, ?, ?, ?, ?)",
        (lat, lon, acq_at, "2026-08-06T12:00:05Z", run, confirmed),
    )
    conn.commit()


def _source_fixe(conn, *, lat, lon):
    conn.execute(
        "INSERT INTO fixed_source (lat, lon, status) VALUES (?, ?, 'confirme')", (lat, lon)
    )
    conn.commit()


def _persistant(conn, lat=44.70, lon=-1.00):
    """2 slots distincts au même endroit (display_min_detections = 2)."""
    _det(conn, lat=lat, lon=lon, acq_at="2026-08-06T11:40:00Z")
    _det(conn, lat=lat + 0.001, lon=lon, acq_at="2026-08-06T11:50:00Z")


def test_signal_persistant_affiche(conn, config):
    _persistant(conn)
    fc = geo_signals_geojson(conn, config, clock=NOW)
    assert len(fc["features"]) == 1
    f = fc["features"][0]
    assert f["properties"]["couche"] == "signal_geo"
    assert "en attente de confirmation" in f["properties"]["libelle"]
    assert "public_id" not in f["properties"]
    assert f["geometry"]["type"] == "Polygon"        # carré, pas un point


def test_un_seul_slot_pas_affiche(conn, config):
    _det(conn, lat=44.70, lon=-1.00, acq_at="2026-08-06T11:50:00Z")   # 1 slot < 2
    assert geo_signals_geojson(conn, config, clock=NOW)["features"] == []


def test_masque_par_source_fixe_confirmee(conn, config):
    _persistant(conn, lat=44.70, lon=-1.00)
    _source_fixe(conn, lat=44.70, lon=-1.00)          # torchère au même endroit
    assert geo_signals_geojson(conn, config, clock=NOW)["features"] == []


def test_vrai_feu_pres_industrie_reste_affiche(conn, config):
    """Une source fixe à ~5 km (> display_fixed_source_radius_m ≈ 2,5 km) ne masque pas un vrai amas."""
    _persistant(conn, lat=44.70, lon=-1.00)
    _source_fixe(conn, lat=44.745, lon=-1.00)         # ~5 km au nord
    assert len(geo_signals_geojson(conn, config, clock=NOW)["features"]) == 1


def test_confirmees_exclues(conn, config):
    fid = conn.execute(
        "INSERT INTO fire_event (created_at, lifecycle) VALUES ('2026-08-06T12:00:00Z','actif')"
    ).lastrowid
    _det(conn, lat=44.70, lon=-1.00, acq_at="2026-08-06T11:40:00Z", confirmed=fid)
    _det(conn, lat=44.70, lon=-1.001, acq_at="2026-08-06T11:50:00Z", confirmed=fid)
    assert geo_signals_geojson(conn, config, clock=NOW)["features"] == []


def test_anciennes_exclues(conn, config):
    # > display_max_h (24 h) avant NOW → hors fenêtre d'affichage
    _det(conn, lat=44.70, lon=-1.00, acq_at="2026-08-05T06:00:00Z")
    _det(conn, lat=44.70, lon=-1.001, acq_at="2026-08-05T06:10:00Z")
    assert geo_signals_geojson(conn, config, clock=NOW)["features"] == []


def test_aucune_detection(conn, config):
    assert geo_signals_geojson(conn, config, clock=NOW) == {"type": "FeatureCollection", "features": []}
