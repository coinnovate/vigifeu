"""Tests du rattrapage FIRMS (Spec 02 §2 fetch_firms_backfill, §10.4 panne simulée).

La panne de FIRMS est simulée par un mock qui lève, puis un mock qui réussit
(sensible au jour demandé pour que chaque jour porte des hotspots distincts).
"""

from __future__ import annotations

from datetime import date

import pytest

from vigifeu.ingest import firms
from vigifeu.model.db import active_sources

_HEADER = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,"
    "satellite,instrument,confidence,version,bright_ti5,frp,daynight"
)


class FakeResponse:
    status_code = 200

    def __init__(self, text: str):
        self.text = text


def _csv_pour_jour(day_iso: str) -> str:
    """Un hotspot dont l'acq_date est le jour demandé (rows distinctes par jour)."""
    return (
        f"{_HEADER}\n"
        f"44.9161,-1.1483,367.0,0.39,0.36,{day_iso},1232,N,VIIRS,h,2.0NRT,290.1,12.5,D\n"
    )


def _mock_succes(url: str, *a, **k) -> FakeResponse:
    day_iso = url.rstrip("/").split("/")[-1]
    return FakeResponse(_csv_pour_jour(day_iso))


def _mock_panne(*a, **k):
    raise firms.FirmsError("HTTP 503 (réessayable)")


@pytest.fixture()
def env(db, monkeypatch):
    conn, config = db
    monkeypatch.setenv("FIRMS_MAP_KEY", "clef-de-test")
    return conn, config, monkeypatch


TODAY = date(2026, 7, 27)


def _ingest_jours(conn, config, jours, sources):
    for src in sources:
        for day in jours:
            firms.ingest_day(conn, config, src, day)


def test_detection_des_trous(env):
    """Seuls les jours sans run réussi sont des trous ; un jour réussi n'en est pas un."""
    conn, config, mp = env
    srcs = active_sources(conn)

    # Tous les jours de la fenêtre réussissent SAUF le 25 et le 26.
    mp.setattr(firms, "_fetch_csv", _mock_succes)
    ok_days = [date(2026, 7, d) for d in (20, 21, 22, 23, 24, 27)]
    _ingest_jours(conn, config, ok_days, srcs)

    # 25 et 26 : la panne (que des runs en échec).
    mp.setattr(firms, "_fetch_csv", _mock_panne)
    _ingest_jours(conn, config, [date(2026, 7, 25), date(2026, 7, 26)], srcs)

    gaps = firms.days_needing_backfill(conn, config, TODAY)
    jours_trous = {day for _, day in gaps}
    assert jours_trous == {date(2026, 7, 25), date(2026, 7, 26)}
    # Un trou par satellite actif.
    assert len(gaps) == 2 * len(srcs)


def test_panne_6h_rattrapee(env):
    """Spec 02 §10.4 : après la panne, le backfill comble les jours, la donnée arrive,
    et le trou reste visible dans ingestion_run."""
    conn, config, mp = env
    srcs = active_sources(conn)

    mp.setattr(firms, "_fetch_csv", _mock_succes)
    _ingest_jours(conn, config, [date(2026, 7, d) for d in (20, 21, 22, 23, 24, 27)], srcs)

    mp.setattr(firms, "_fetch_csv", _mock_panne)
    _ingest_jours(conn, config, [date(2026, 7, 25), date(2026, 7, 26)], srcs)

    # Aucun hotspot pour le 25/26 pendant la panne.
    n_25 = conn.execute(
        "SELECT COUNT(*) AS n FROM hotspot_raw WHERE acq_at LIKE '2026-07-25%'"
    ).fetchone()["n"]
    assert n_25 == 0

    # FIRMS revient : rattrapage.
    mp.setattr(firms, "_fetch_csv", _mock_succes)
    results = firms.fetch_firms_backfill(conn, config, today=TODAY)

    assert all(r["status"] == "ok" for r in results)
    # Les jours en panne portent maintenant des hotspots.
    for jour in ("2026-07-25", "2026-07-26"):
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM hotspot_raw WHERE acq_at LIKE ?", (f"{jour}%",)
        ).fetchone()["n"]
        assert n == len(srcs)  # un hotspot par satellite

    # Plus aucun trou.
    assert firms.days_needing_backfill(conn, config, TODAY) == []

    # Le trou reste tracé : les runs en échec de la panne sont toujours là.
    n_echecs = conn.execute(
        "SELECT COUNT(*) AS n FROM ingestion_run WHERE status='error'"
    ).fetchone()["n"]
    assert n_echecs == 2 * len(srcs)


def test_backfill_idempotent(env):
    """Sans trou, le backfill ne fait rien."""
    conn, config, mp = env
    srcs = active_sources(conn)
    mp.setattr(firms, "_fetch_csv", _mock_succes)
    # Couvrir toute la fenêtre [today-7, today].
    _ingest_jours(conn, config, [date(2026, 7, d) for d in range(20, 28)], srcs)

    assert firms.days_needing_backfill(conn, config, TODAY) == []
    assert firms.fetch_firms_backfill(conn, config, today=TODAY) == []
