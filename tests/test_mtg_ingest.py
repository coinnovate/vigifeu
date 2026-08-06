"""Tests du cycle d'ingestion MTG `fetch_mtg_fir` (Spec 07 §2/§4, étape 4).

Réseau + netCDF simulés : un client factice (list_products/download) et `parse_listproduct`
monkeypatché (le parsing est déjà couvert à l'étape 3). On couvre : ingestion de base + journal,
idempotence, immuabilité de `ingested_at`, désactivation, non-blocage sur erreur de source,
saut d'un granule illisible, et fenêtre repartant du dernier succès.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vigifeu.ingest import mtg
from vigifeu.model.db import connect, load_config, migrate

NOW1 = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)
NOW2 = datetime(2026, 8, 6, 12, 15, 0, tzinfo=UTC)

PX = [
    {"lat": 44.7, "lon": -1.0, "acq_at": "2026-08-06T11:50:00Z",
     "frp_mw": 12.5, "frp_uncertainty_mw": None, "confidence": "1"},
    {"lat": 44.8, "lon": -1.1, "acq_at": "2026-08-06T11:50:00Z",
     "frp_mw": 30.0, "frp_uncertainty_mw": 3.0, "confidence": "2"},
]
PX2 = [
    {"lat": 48.0, "lon": 2.0, "acq_at": "2026-08-06T12:00:00Z",
     "frp_mw": 5.0, "frp_uncertainty_mw": None, "confidence": "1"},
]
PIXELS = {b"u1": PX, b"u2": PX2}


class FakeClient:
    def __init__(self, produits, *, fail_urls=None, list_boom=False):
        self.produits = produits
        self.fail_urls = set(fail_urls or [])
        self.list_boom = list_boom
        self.listed: list[tuple] = []

    def list_products(self, since, until):
        self.listed.append((since, until))
        if self.list_boom:
            raise RuntimeError("boom listing")
        return self.produits

    def download(self, url):
        if url in self.fail_urls:
            raise RuntimeError("boom download")
        return url.encode()


def _produit(pid, url, sensing="2026-08-06T11:50:00Z"):
    return {"product_id": pid, "sensing_at": sensing, "download_url": url}


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture()
def config():
    cfg = load_config("config/params.toml")
    cfg["mtg"]["activated"] = True   # activé pour les tests (défaut prod = false)
    return cfg


@pytest.fixture(autouse=True)
def _fake_parse(monkeypatch):
    """parse_listproduct → pixels fixes selon les octets téléchargés (isolé du vrai netCDF)."""
    monkeypatch.setattr(mtg, "parse_listproduct", lambda data, config, **kw: PIXELS.get(data, []))


def _rows(conn):
    return conn.execute("SELECT * FROM geo_detection_raw ORDER BY lat").fetchall()


def test_ingestion_de_base_et_journal(conn, config):
    client = FakeClient([_produit("p1", "u1"), _produit("p2", "u2")])
    stats = mtg.fetch_mtg_fir(conn, config, client=client, clock=NOW1)
    assert stats == {"status": "ok", "n_products": 2, "n_rows": 3, "n_new": 3, "n_err": 0}
    assert len(_rows(conn)) == 3
    run = conn.execute(
        "SELECT * FROM ingestion_run WHERE source='mtg:0682'"
    ).fetchone()
    assert run["status"] == "ok" and run["n_new"] == 3
    assert '"until": "2026-08-06T12:00:00Z"' in run["params"]


def test_idempotence(conn, config):
    client = FakeClient([_produit("p1", "u1"), _produit("p2", "u2")])
    mtg.fetch_mtg_fir(conn, config, client=client, clock=NOW1)
    stats2 = mtg.fetch_mtg_fir(conn, config, client=client, clock=NOW2)
    assert stats2["n_new"] == 0 and stats2["n_rows"] == 3
    assert len(_rows(conn)) == 3   # aucune ligne dupliquée


def test_ingested_at_jamais_reecrit(conn, config, monkeypatch):
    holder = {"t": "2026-08-06T12:00:05Z"}
    monkeypatch.setattr(mtg, "_now_iso", lambda: holder["t"])
    client = FakeClient([_produit("p1", "u1")])
    mtg.fetch_mtg_fir(conn, config, client=client, clock=NOW1)
    holder["t"] = "2026-08-06T12:15:05Z"          # le temps avance
    mtg.fetch_mtg_fir(conn, config, client=client, clock=NOW2)   # rejeu des mêmes pixels
    ingested = {r["ingested_at"] for r in _rows(conn)}
    assert ingested == {"2026-08-06T12:00:05Z"}   # gardé, pas réécrit (mesure de latence)


def test_desactive_ne_fait_rien(conn, config):
    config["mtg"]["activated"] = False
    client = FakeClient([_produit("p1", "u1")])
    stats = mtg.fetch_mtg_fir(conn, config, client=client, clock=NOW1)
    assert stats["n_new"] == 0 and stats["n_products"] == 0
    assert len(_rows(conn)) == 0
    run = conn.execute("SELECT status, error_text FROM ingestion_run WHERE source='mtg:0682'").fetchone()
    assert run["status"] == "ok" and "désactivé" in run["error_text"]


def test_erreur_source_non_bloquante(conn, config):
    client = FakeClient([], list_boom=True)
    stats = mtg.fetch_mtg_fir(conn, config, client=client, clock=NOW1)   # ne lève pas
    assert stats["status"] == "error"
    assert len(_rows(conn)) == 0
    run = conn.execute("SELECT status, error_text FROM ingestion_run WHERE source='mtg:0682'").fetchone()
    assert run["status"] == "error" and "boom listing" in run["error_text"]


def test_granule_en_echec_saute(conn, config):
    """u2 échoue au téléchargement : u1 est quand même ingéré, l'échec est compté."""
    client = FakeClient([_produit("p1", "u1"), _produit("p2", "u2")], fail_urls={"u2"})
    stats = mtg.fetch_mtg_fir(conn, config, client=client, clock=NOW1)
    assert stats["status"] == "ok" and stats["n_new"] == 2 and stats["n_err"] == 1
    assert len(_rows(conn)) == 2


def test_fenetre_repart_du_dernier_succes(conn, config):
    c1 = FakeClient([_produit("p1", "u1")])
    mtg.fetch_mtg_fir(conn, config, client=c1, clock=NOW1)   # until = NOW1
    c2 = FakeClient([_produit("p2", "u2")])
    mtg.fetch_mtg_fir(conn, config, client=c2, clock=NOW2)
    # la 2e fenêtre doit démarrer au `until` du 1er run réussi (NOW1), pas à NOW2 - catchup.
    since2, _until2 = c2.listed[0]
    assert since2 == NOW1
