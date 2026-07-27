"""Tests de fire_cell_state (engine/cells.py, Spec 01 §4.3, Spec 02 §7)."""

from __future__ import annotations

from vigifeu.engine.cells import cell_key, rebuild_cells
from vigifeu.engine.cluster import cluster_new_hotspots
from vigifeu.engine.geo import project

from .conftest import insert_hotspot

STAMP = "2026-07-27T00:00:00Z"


def test_cell_key_grille_750m():
    """Indice de grille sur coordonnées projetées : quantification par pas de 750 m."""
    assert cell_key(1000, 1000, 750) == cell_key(1200, 1400, 750)   # même case (1,1)
    assert cell_key(1000, 1000, 750) != cell_key(1600, 1000, 750)   # case (2,1)
    assert cell_key(1000, 1000, 750) != cell_key(1000, 1600, 750)   # case (1,2)


def test_agregation_et_etats(db):
    """Cellule ancienne ⇒ plus_detecte ; cellule du dernier passage ⇒ front_actif.

    Deux cellules d'un même feu : A (44.900) détectée le 22 à 00:00, B (44.910,
    ~1,1 km au nord, donc case distincte mais < D_link) redétectée le 23 à 12:00."""
    conn, config = db
    insert_hotspot(conn, 44.900, -1.020, "2026-07-22T00:00:00Z", frp=20.0, overpass_id=1)
    insert_hotspot(conn, 44.910, -1.020, "2026-07-22T00:10:00Z", frp=5.0, overpass_id=1)
    insert_hotspot(conn, 44.910, -1.020, "2026-07-23T12:00:00Z", frp=80.0, overpass_id=2)
    cluster_new_hotspots(conn, config, stamp=STAMP)
    assert conn.execute("SELECT COUNT(*) AS n FROM fire_event").fetchone()["n"] == 1
    eid = conn.execute("SELECT id FROM fire_event").fetchone()["id"]

    counts = rebuild_cells(conn, config, eid)
    rows = {
        r["cell_key"]: r
        for r in conn.execute("SELECT * FROM fire_cell_state WHERE fire_event_id=?", (eid,))
    }
    assert len(rows) == 2
    key_a = cell_key(*project(44.900, -1.020), 750)
    key_b = cell_key(*project(44.910, -1.020), 750)
    # Horloge = 23/07 12:00. A (22/07 00:00) silencieuse > 24 h ⇒ plus_detecte ;
    # B (23/07 12:00) ⇒ front_actif.
    assert rows[key_a]["state"] == "plus_detecte"
    assert rows[key_b]["state"] == "front_actif"
    assert rows[key_b]["frp_max_mw"] == 80.0
    assert counts == {"front_actif": 1, "recent": 0, "plus_detecte": 1}


def test_first_last_acq_par_cellule(db):
    """first/last_acq d'une cellule couvrent toutes ses détections (mêmes coords)."""
    conn, config = db
    insert_hotspot(conn, 44.900, -1.020, "2026-07-22T12:00:00Z", overpass_id=1)
    insert_hotspot(conn, 44.900, -1.020, "2026-07-22T13:40:00Z", overpass_id=2)
    cluster_new_hotspots(conn, config, stamp=STAMP)
    eid = conn.execute("SELECT id FROM fire_event").fetchone()["id"]
    rebuild_cells(conn, config, eid)
    rows = conn.execute("SELECT * FROM fire_cell_state WHERE fire_event_id=?", (eid,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["first_acq_at"] == "2026-07-22T12:00:00Z"
    assert rows[0]["last_acq_at"] == "2026-07-22T13:40:00Z"


def test_rebuild_idempotent(db):
    """Recalcul complet (état courant) : deux appels donnent le même résultat."""
    conn, config = db
    insert_hotspot(conn, 44.900, -1.020, "2026-07-22T12:00:00Z", overpass_id=1)
    insert_hotspot(conn, 44.910, -1.020, "2026-07-22T13:40:00Z", overpass_id=2)
    cluster_new_hotspots(conn, config, stamp=STAMP)
    eid = conn.execute("SELECT id FROM fire_event").fetchone()["id"]
    c1 = rebuild_cells(conn, config, eid)
    n1 = conn.execute("SELECT COUNT(*) AS n FROM fire_cell_state").fetchone()["n"]
    c2 = rebuild_cells(conn, config, eid)
    n2 = conn.execute("SELECT COUNT(*) AS n FROM fire_cell_state").fetchone()["n"]
    assert c1 == c2
    assert n1 == n2 == 2
