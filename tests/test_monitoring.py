"""Tests du monitoring (Spec 02 §9) : trou de collecte et pings healthchecks."""

from __future__ import annotations

import httpx

from vigifeu.model import monitoring


def _run(conn, source, started, finished, status):
    conn.execute(
        "INSERT INTO ingestion_run (source, started_at, finished_at, status) VALUES (?,?,?,?)",
        (source, started, finished, status),
    )
    conn.commit()


def test_derniere_collecte_reussie(db):
    conn, _ = db
    _run(conn, "firms:VIIRS_SNPP_NRT", "2026-07-27T10:00:00Z", "2026-07-27T10:00:30Z", "ok")
    _run(conn, "firms:VIIRS_SNPP_NRT", "2026-07-27T10:15:00Z", "2026-07-27T10:15:20Z", "ok")
    _run(conn, "firms:VIIRS_SNPP_NRT", "2026-07-27T10:30:00Z", None, "error")  # échec ignoré
    assert monitoring.last_successful_collection_at(conn) == "2026-07-27T10:15:20Z"


def test_pas_de_trou_si_recent(db):
    conn, config = db
    _run(conn, "firms:VIIRS_SNPP_NRT", "2026-07-27T09:00:00Z", "2026-07-27T09:00:30Z", "ok")
    r = monitoring.check_collection_gap(conn, config, now_iso="2026-07-27T18:00:00Z")  # ~9 h
    assert r["alert"] is False
    assert r["gap_hours"] < 24


def test_trou_declenche_alerte(db):
    conn, config = db
    _run(conn, "firms:VIIRS_SNPP_NRT", "2026-07-26T02:00:00Z", "2026-07-26T02:00:30Z", "ok")
    # 30 h plus tard, aucune réussite depuis.
    r = monitoring.check_collection_gap(conn, config, now_iso="2026-07-27T08:00:30Z")
    assert r["alert"] is True
    assert r["gap_hours"] >= 30
    assert "trou de collecte" in r["message"]


def test_aucune_collecte_est_une_alerte(db):
    conn, config = db
    r = monitoring.check_collection_gap(conn, config, now_iso="2026-07-27T08:00:00Z")
    assert r["alert"] is True
    assert r["last_at"] is None
    assert "aucune" in r["message"]


def test_seuil_configurable(db):
    conn, config = db
    _run(conn, "firms:VIIRS_SNPP_NRT", "2026-07-27T00:00:00Z", "2026-07-27T00:00:30Z", "ok")
    config = {**config, "monitoring": {"gap_alert_hours": 6}}
    r = monitoring.check_collection_gap(conn, config, now_iso="2026-07-27T08:00:30Z")  # 8 h > 6 h
    assert r["alert"] is True


def test_ping_sans_url_est_noop(db):
    assert monitoring.ping_healthcheck(None) is False
    assert monitoring.ping_healthcheck("") is False


def test_ping_ne_leve_jamais(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("réseau injoignable")

    monkeypatch.setattr(httpx, "get", boom)
    assert monitoring.ping_healthcheck("https://hc.example/abc") is False  # échoue en silence


def test_ping_fail_suffixe(monkeypatch):
    appels = []
    monkeypatch.setattr(httpx, "get", lambda url, **k: appels.append(url))
    monitoring.ping_healthcheck("https://hc.example/abc", ok=True)
    monitoring.ping_healthcheck("https://hc.example/abc/", ok=False)
    assert appels == ["https://hc.example/abc", "https://hc.example/abc/fail"]
