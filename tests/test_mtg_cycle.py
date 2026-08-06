"""Orchestration du cycle MTG `run_mtg_cycle` (Spec 07 §9, étape 9).

Assemble ingestion → confirmation → candidats. Vérifie l'enchaînement de bout en bout sur un
feu VIIRS présent (confirmation) et sur un amas sans feu (amorçage), et le calcul de `fires`/`carte`
qui pilote la régénération. Réseau + netCDF simulés (client factice, parse monkeypatché).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vigifeu.ingest import mtg
from vigifeu.model.db import connect, load_config, migrate

NOW = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)

# Deux amas : l'un sur un feu VIIRS (→ confirmé), l'autre isolé et persistant (→ candidat).
PRES_FEU = [
    {"lat": 44.705, "lon": -1.00, "acq_at": "2026-08-06T11:40:00Z", "frp_mw": 12.0,
     "frp_uncertainty_mw": None, "confidence": "1"},
    {"lat": 44.706, "lon": -1.00, "acq_at": "2026-08-06T11:50:00Z", "frp_mw": 15.0,
     "frp_uncertainty_mw": None, "confidence": "1"},
]
ISOLE = [
    {"lat": 46.00, "lon": 3.00, "acq_at": "2026-08-06T11:40:00Z", "frp_mw": 8.0,
     "frp_uncertainty_mw": None, "confidence": "1"},
    {"lat": 46.001, "lon": 3.00, "acq_at": "2026-08-06T11:50:00Z", "frp_mw": 9.0,
     "frp_uncertainty_mw": None, "confidence": "1"},
    {"lat": 46.000, "lon": 3.001, "acq_at": "2026-08-06T12:00:00Z", "frp_mw": 10.0,
     "frp_uncertainty_mw": None, "confidence": "1"},
]
PIXELS = {b"u1": PRES_FEU + ISOLE}


class FakeClient:
    def list_products(self, since, until):
        return [{"product_id": "p1", "sensing_at": "2026-08-06T11:50:00Z", "download_url": "u1"}]

    def download(self, url):
        return url.encode()


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture()
def config():
    cfg = load_config("config/params.toml")
    cfg["mtg"]["activated"] = True
    return cfg


@pytest.fixture(autouse=True)
def _fake_parse(monkeypatch):
    monkeypatch.setattr(mtg, "parse_fir", lambda data, config, **kw: PIXELS.get(data, []))


def _feu_viirs(conn):
    fid = conn.execute(
        "INSERT INTO fire_event (created_at, first_acq_at, last_acq_at, lifecycle) "
        "VALUES ('2026-08-06T11:00:00Z','2026-08-06T11:00:00Z','2026-08-06T11:45:00Z','actif')"
    ).lastrowid
    conn.execute(
        "INSERT INTO fire_cell_state (fire_event_id, cell_key, lat, lon) VALUES (?,?,44.705,-1.00)",
        (fid, "c1"),
    )
    conn.commit()
    return fid


def test_cycle_confirme_et_amorce(conn, config):
    fid = _feu_viirs(conn)
    res = mtg.run_mtg_cycle(conn, config, client=FakeClient(), clock=NOW)
    # ingestion : 5 détections
    assert res["fetch"]["n_new"] == 5
    # confirmation : les 2 détections près du feu VIIRS
    assert res["confirm"]["n_confirmed"] == 2
    assert res["fires"] == [fid]                      # feu à régénérer
    # amorçage : l'amas isolé (3 slots) devient un candidat en_attente
    assert res["candidates"]["crees"] == 1
    assert res["carte"] is True
    # état en base : 2 détections confirmées, 1 candidat
    n_conf = conn.execute(
        "SELECT COUNT(*) AS n FROM geo_detection_raw WHERE confirmed_by_fire_event_id=?", (fid,)
    ).fetchone()["n"]
    assert n_conf == 2
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM geo_candidate WHERE status='en_attente'"
    ).fetchone()["n"] == 1


def test_cycle_idempotent_rien_a_regenerer(conn, config):
    _feu_viirs(conn)
    mtg.run_mtg_cycle(conn, config, client=FakeClient(), clock=NOW)
    res2 = mtg.run_mtg_cycle(conn, config, client=FakeClient(), clock=NOW)  # mêmes granules
    assert res2["fetch"]["n_new"] == 0
    assert res2["confirm"]["n_confirmed"] == 0
    assert res2["fires"] == []
    assert res2["carte"] is False                     # rien de neuf → pas de régén


def test_cycle_desactive_noop(conn, config):
    config["mtg"]["activated"] = False
    res = mtg.run_mtg_cycle(conn, config, client=FakeClient(), clock=NOW)
    assert res["fetch"]["n_new"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM geo_detection_raw").fetchone()["n"] == 0
