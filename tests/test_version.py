"""Tests des versions et mesures factuelles (engine/version.py, Spec 02 §6).

Le rejeu Saumos porte les deux mesures-clés du jalon (§10.1) : progression du
front ~5,5 km nord entre les nuits du 24 et du 25, et chute d'intensité nuit/nuit
d'un facteur > 10.
"""

from __future__ import annotations

import json

from vigifeu.engine.cells import rebuild_cells
from vigifeu.engine.cluster import cluster_new_hotspots
from vigifeu.engine.overpass import build_overpasses
from vigifeu.engine.version import (
    create_version,
    front_progress,
    intensity_series,
)

from .conftest import insert_hotspot, load_saumos_hotspots

STAMP = "2026-07-27T00:00:00Z"


def _prepare(conn, config):
    build_overpasses(conn, config)
    cluster_new_hotspots(conn, config, stamp=STAMP)
    eid = conn.execute("SELECT id FROM fire_event ORDER BY id LIMIT 1").fetchone()["id"]
    rebuild_cells(conn, config, eid)
    return eid


# ---------------------------------------------------------------- synthétique

def test_front_progress_nord(db):
    """Deux passages de nuit à 24 h : le centroïde avance ~1,1 km vers le nord."""
    conn, config = db
    insert_hotspot(conn, 44.900, -1.020, "2026-07-22T01:00:00Z", day_night="N", overpass_id=None)
    insert_hotspot(conn, 44.910, -1.020, "2026-07-23T01:00:00Z", day_night="N", overpass_id=None)
    eid = _prepare(conn, config)

    last = conn.execute(
        "SELECT id FROM overpass ORDER BY window_start DESC LIMIT 1"
    ).fetchone()["id"]
    prog = front_progress(conn, eid, last)
    assert 0.9 < prog["km"] < 1.3
    assert 0.9 < prog["north_km"] < 1.3           # progression vers le nord
    assert prog["bearing"] < 30 or prog["bearing"] > 330


def test_front_progress_absent_sans_passage_comparable(db):
    """Un seul passage de nuit : pas de comparaison possible, progression nulle."""
    conn, config = db
    insert_hotspot(conn, 44.900, -1.020, "2026-07-22T01:00:00Z", day_night="N", overpass_id=None)
    eid = _prepare(conn, config)
    last = conn.execute("SELECT id FROM overpass ORDER BY window_start DESC LIMIT 1").fetchone()["id"]
    prog = front_progress(conn, eid, last)
    assert prog["km"] == 0.0 and prog["bearing"] is None


def test_create_version_contenu(db):
    """Une version porte géométrie, comptages, FRP du dernier passage, et fe_hotspot."""
    conn, config = db
    insert_hotspot(conn, 44.900, -1.020, "2026-07-22T12:00:00Z", frp=50.0, overpass_id=None)
    insert_hotspot(conn, 44.905, -1.020, "2026-07-22T13:40:00Z", frp=80.0, overpass_id=None)
    eid = _prepare(conn, config)

    vid = create_version(conn, config, eid, stamp=STAMP)
    v = conn.execute("SELECT * FROM fire_event_version WHERE id=?", (vid,)).fetchone()
    assert v["version_n"] == 1
    assert v["n_hotspots"] == 2
    assert v["geometry_wkt"] is not None
    n_feh = conn.execute(
        "SELECT COUNT(*) AS n FROM fe_hotspot WHERE fire_event_version_id=?", (vid,)
    ).fetchone()["n"]
    assert n_feh == 2
    # version_n s'incrémente au versionnage suivant.
    vid2 = create_version(conn, config, eid, stamp=STAMP)
    assert conn.execute("SELECT version_n FROM fire_event_version WHERE id=?", (vid2,)).fetchone()["version_n"] == 2


# ---------------------------------------------------------------- rejeu réel

def _saumos_event(conn, config):
    load_saumos_hotspots(conn, bbox=(44.5, 45.3, -1.30, -0.30))
    build_overpasses(conn, config)
    cluster_new_hotspots(conn, config, stamp=STAMP)
    eid = conn.execute(
        "SELECT fire_event_id FROM hotspot_raw WHERE acq_at='2026-07-22T11:55:00Z' "
        "AND lat BETWEEN 44.88 AND 44.92 AND lon BETWEEN -1.05 AND -0.99 LIMIT 1"
    ).fetchone()["fire_event_id"]
    rebuild_cells(conn, config, eid)
    return eid


def test_saumos_progression_nord_24_vers_25(db):
    """Jalon : le front progresse ~5,5 km vers le nord entre les nuits du 24 et 25."""
    conn, config = db
    eid = _saumos_event(conn, config)
    # Première passe de nuit du 25/07 : sa référence comparable est la dernière
    # passe de nuit du 24/07 (~22 h avant).
    passage = conn.execute(
        "SELECT id FROM overpass WHERE day_night='N' AND window_start LIKE '2026-07-25%' "
        "ORDER BY window_start LIMIT 1"
    ).fetchone()["id"]
    prog = front_progress(conn, eid, passage)
    assert 4.5 < prog["north_km"] < 7.0          # ~5,9 km, composante nord (jalon ~5,5)
    assert prog["bearing"] < 90 or prog["bearing"] > 270


def test_saumos_chute_intensite_nuit_sur_nuit(db):
    """Jalon : l'intensité nuit/nuit chute d'un facteur > 10 sur la durée du feu."""
    conn, config = db
    eid = _saumos_event(conn, config)
    series = intensity_series(conn, eid, config)
    nuits = [s["frp"] for s in series if s["dn"] == "N" and s["frp"] > 0]
    assert max(nuits) > 10 * nuits[-1]           # pic vs dernière nuit


def test_saumos_version_stats_intensite(db):
    """La version stocke la série d'intensité (support des courbes nuit/nuit)."""
    conn, config = db
    eid = _saumos_event(conn, config)
    vid = create_version(conn, config, eid, stamp=STAMP)
    stats = json.loads(
        conn.execute("SELECT stats_json FROM fire_event_version WHERE id=?", (vid,)).fetchone()["stats_json"]
    )
    assert any(p["dn"] == "N" for p in stats["intensity"])
    assert len(stats["config"]) == 12
