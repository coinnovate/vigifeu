"""Méthodologie chiffrée : latence NRT mesurée, avec exclusion des jours re-téléchargés."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from vigifeu.generate.pages import latence_nrt_stats


def _hotspot(conn, src, run, acq, ing, lon):
    conn.execute(
        "INSERT INTO hotspot_raw (source_id, lat, lon, acq_at, ingested_at, ingestion_run_id) "
        "VALUES (?, 44.0, ?, ?, ?, ?)",
        (src, lon, acq.isoformat().replace("+00:00", "Z"), ing.isoformat().replace("+00:00", "Z"), run),
    )


def test_latence_nrt_exclut_le_backfill(db):
    conn, config = db
    config["generate"]["latence_nrt_max_h"] = 6
    src = conn.execute("SELECT id FROM satellite_source LIMIT 1").fetchone()["id"]
    run = conn.execute(
        "INSERT INTO ingestion_run (source, started_at, status) VALUES ('t', ?, 'ok')",
        ("2026-07-26T00:00:00Z",),
    ).lastrowid
    base = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    # 60 détections temps réel à 90 min de latence…
    for k in range(60):
        _hotspot(conn, src, run, base, base + timedelta(minutes=90), lon=-1.0 - k * 0.01)
    # …et 10 re-téléchargées (latence de 4 jours) qui doivent être exclues.
    for k in range(10):
        _hotspot(conn, src, run, base, base + timedelta(days=4), lon=-5.0 - k * 0.01)
    conn.commit()

    s = latence_nrt_stats(conn, config)
    assert s is not None
    assert s["mediane_min"] == 90 and s["p90_min"] == 90   # les backfillés (4 j) écartés
    assert s["n"] == "60"                                   # 60 mesures retenues, pas 70


def test_latence_nrt_none_si_trop_peu(db):
    conn, config = db
    src = conn.execute("SELECT id FROM satellite_source LIMIT 1").fetchone()["id"]
    run = conn.execute(
        "INSERT INTO ingestion_run (source, started_at, status) VALUES ('t', ?, 'ok')",
        ("2026-07-26T00:00:00Z",),
    ).lastrowid
    base = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    for k in range(5):   # < 50 mesures → pas de chiffre crédible
        _hotspot(conn, src, run, base, base + timedelta(minutes=90), lon=-1.0 - k * 0.01)
    conn.commit()
    assert latence_nrt_stats(conn, config) is None
