"""Tests du Lot 0.

Le test d'idempotence préfigure le test Spec 02 §10.3 : double ingestion
d'un même jour = zéro nouvelle ligne. Les appels réseau sont simulés
(le CSV de test reproduit le format FIRMS réel).
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from vigifeu.ingest import firms
from vigifeu.model.db import (
    active_sources,
    connect,
    load_config,
    migrate,
    sync_satellite_sources,
)

# Format réel de l'API area FIRMS (VIIRS NRT)
CSV_FIXTURE = """\
latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight
44.9161,-1.1483,367.0,0.39,0.36,2026-07-22,1232,N,VIIRS,h,2.0NRT,290.1,12.5,D
44.9204,-1.1441,340.2,0.39,0.36,2026-07-22,1232,N,VIIRS,n,2.0NRT,289.7,6.3,D
44.9250,-1.1400,331.0,0.39,0.36,2026-07-22,1232,N,VIIRS,l,2.0NRT,288.0,,D
"""


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    config = load_config("config/params.toml")
    sync_satellite_sources(c, config)
    yield c, config
    c.close()


class FakeResponse:
    status_code = 200

    def __init__(self, text: str):
        self.text = text


def test_migrations_appliquees(conn):
    c, _ = conn
    v = c.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"]
    assert v >= 1


def test_sources_depuis_config(conn):
    c, _ = conn
    codes = {r["code"] for r in active_sources(c)}
    assert {"VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"} <= codes


def test_ingestion_idempotente(conn, monkeypatch):
    """Double ingestion du même jour = zéro nouvelle ligne (Spec 02 §10.3)."""
    c, config = conn
    monkeypatch.setenv("FIRMS_MAP_KEY", "clef-de-test")
    monkeypatch.setattr(firms, "_fetch_csv", lambda *a, **k: FakeResponse(CSV_FIXTURE))
    src = active_sources(c)[0]

    r1 = firms.ingest_day(c, config, src, date(2026, 7, 22))
    assert r1 == {"status": "ok", "n_rows": 3, "n_new": 3}

    r2 = firms.ingest_day(c, config, src, date(2026, 7, 22))
    assert r2 == {"status": "ok", "n_rows": 3, "n_new": 0}

    n = c.execute("SELECT COUNT(*) AS n FROM hotspot_raw").fetchone()["n"]
    assert n == 3


def test_ingested_at_non_reecrit(conn, monkeypatch):
    """Une ligne déjà connue garde son ingested_at d'origine (P3 : la latence ne se rejoue pas)."""
    c, config = conn
    monkeypatch.setenv("FIRMS_MAP_KEY", "clef-de-test")
    monkeypatch.setattr(firms, "_fetch_csv", lambda *a, **k: FakeResponse(CSV_FIXTURE))
    src = active_sources(c)[0]

    firms.ingest_day(c, config, src, date(2026, 7, 22))
    avant = [r["ingested_at"] for r in c.execute("SELECT ingested_at FROM hotspot_raw ORDER BY id")]
    firms.ingest_day(c, config, src, date(2026, 7, 22))
    apres = [r["ingested_at"] for r in c.execute("SELECT ingested_at FROM hotspot_raw ORDER BY id")]
    assert avant == apres


def test_acq_at_iso(conn, monkeypatch):
    c, config = conn
    monkeypatch.setenv("FIRMS_MAP_KEY", "clef-de-test")
    monkeypatch.setattr(firms, "_fetch_csv", lambda *a, **k: FakeResponse(CSV_FIXTURE))
    src = active_sources(c)[0]
    firms.ingest_day(c, config, src, date(2026, 7, 22))
    r = c.execute("SELECT acq_at, frp_mw, day_night FROM hotspot_raw ORDER BY id LIMIT 1").fetchone()
    assert r["acq_at"] == "2026-07-22T12:32:00Z"
    assert r["frp_mw"] == 12.5
    assert r["day_night"] == "D"


def test_erreur_source_journalisee_jamais_levee(conn, monkeypatch):
    """Spec 02 P3 : l'échec d'une source ne casse pas le cycle, il est journalisé."""
    c, config = conn
    monkeypatch.setenv("FIRMS_MAP_KEY", "clef-de-test")

    def boom(*a, **k):
        raise firms.FirmsError("HTTP 503 (réessayable)")

    monkeypatch.setattr(firms, "_fetch_csv", boom)
    src = active_sources(c)[0]
    r = firms.ingest_day(c, config, src, date(2026, 7, 22))
    assert r["status"] == "error"
    run = c.execute("SELECT * FROM ingestion_run ORDER BY id DESC LIMIT 1").fetchone()
    assert run["status"] == "error"
    assert "503" in run["error_text"]


def test_vue_latence(conn, monkeypatch):
    c, config = conn
    monkeypatch.setenv("FIRMS_MAP_KEY", "clef-de-test")
    monkeypatch.setattr(firms, "_fetch_csv", lambda *a, **k: FakeResponse(CSV_FIXTURE))
    src = active_sources(c)[0]
    firms.ingest_day(c, config, src, date(2026, 7, 22))
    rows = c.execute("SELECT * FROM v_latence_nrt").fetchall()
    assert len(rows) == 3
    assert all(r["latence_h"] is not None for r in rows)


def test_map_key_absente(conn, monkeypatch):
    c, config = conn
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    src = active_sources(c)[0]
    r = firms.ingest_day(c, config, src, date(2026, 7, 22))
    assert r["status"] == "error"
    assert "FIRMS_MAP_KEY" in r["error"]
