"""Tests de l'orchestration du cycle (engine/pipeline.py, Spec 02 §3, §10.3)."""

from __future__ import annotations

from vigifeu.engine.overpass import build_overpasses
from vigifeu.engine.pipeline import process_cycle, reset_interpretation

from .conftest import insert_hotspot, load_saumos_hotspots

STAMP = "2026-07-27T00:00:00Z"


def test_process_cycle_bout_en_bout(db):
    """Un cycle enchaîne marquage→clustering→qualification→version sur un feu franc."""
    conn, config = db
    # Feu franc : 2 passages, 8+ pixels ⇒ vegetation_confirme, donc versionné.
    for i in range(5):
        insert_hotspot(conn, 44.900 + i * 0.002, -1.020, "2026-07-22T12:00:00Z", overpass_id=None)
    for i in range(5):
        insert_hotspot(conn, 44.900 + i * 0.002, -1.020, "2026-07-22T13:40:00Z", overpass_id=None)
    build_overpasses(conn, config)
    res = process_cycle(conn, config, stamp=STAMP)

    assert res["created"] == 1
    assert res["versioned"] == 1
    eid = conn.execute("SELECT id FROM fire_event").fetchone()["id"]
    assert conn.execute(
        "SELECT qualification FROM fire_event WHERE id=?", (eid,)
    ).fetchone()["qualification"] == "vegetation_confirme"
    assert conn.execute("SELECT COUNT(*) AS n FROM fire_event_version").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM fire_cell_state").fetchone()["n"] >= 1


def test_idempotence_process_cycle(db):
    """§10.3 — relancer un cycle sans nouveau hotspot ne crée ni feu ni version."""
    conn, config = db
    for i in range(5):
        insert_hotspot(conn, 44.900 + i * 0.002, -1.020, "2026-07-22T12:00:00Z", overpass_id=None)
    for i in range(5):
        insert_hotspot(conn, 44.900 + i * 0.002, -1.020, "2026-07-22T13:40:00Z", overpass_id=None)
    build_overpasses(conn, config)
    process_cycle(conn, config, stamp=STAMP)

    n_ev = conn.execute("SELECT COUNT(*) AS n FROM fire_event").fetchone()["n"]
    n_ver = conn.execute("SELECT COUNT(*) AS n FROM fire_event_version").fetchone()["n"]

    res2 = process_cycle(conn, config, stamp=STAMP)
    assert res2["created"] == 0 and res2["versioned"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM fire_event").fetchone()["n"] == n_ev
    assert conn.execute("SELECT COUNT(*) AS n FROM fire_event_version").fetchone()["n"] == n_ver


def test_suspect_non_versionne_chaque_cycle(db):
    """Économie §5 : un suspect_isole n'est pas versionné (pas de version créée)."""
    conn, config = db
    insert_hotspot(conn, 44.900, -1.020, "2026-07-22T12:00:00Z", overpass_id=None)
    insert_hotspot(conn, 44.901, -1.021, "2026-07-22T12:00:00Z", overpass_id=None)
    build_overpasses(conn, config)
    res = process_cycle(conn, config, stamp=STAMP)
    assert res["created"] == 1
    assert res["versioned"] == 0   # suspect_isole non versionné
    assert conn.execute("SELECT COUNT(*) AS n FROM fire_event_version").fetchone()["n"] == 0


def test_rejeu_reconstruit_a_l_identique(db):
    """P2 : reset + rejeu reproduit exactement le même découpage et la même
    qualification (rejeu Saumos = pierre de touche)."""
    conn, config = db
    load_saumos_hotspots(conn, bbox=(44.5, 45.3, -1.30, -0.30))
    build_overpasses(conn, config)
    process_cycle(conn, config, stamp=STAMP)

    def snapshot():
        return {
            "events": conn.execute("SELECT COUNT(*) AS n FROM fire_event").fetchone()["n"],
            "confirmes": conn.execute(
                "SELECT COUNT(*) AS n FROM fire_event WHERE qualification='vegetation_confirme'"
            ).fetchone()["n"],
            "membership": conn.execute(
                "SELECT COUNT(*) AS n FROM hotspot_raw WHERE fire_event_id IS NOT NULL"
            ).fetchone()["n"],
        }

    before = snapshot()
    reset_interpretation(conn, config)
    process_cycle(conn, config, stamp=STAMP)
    assert snapshot() == before
