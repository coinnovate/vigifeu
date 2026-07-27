"""Tests du registre des sources fixes (engine/fixed_source.py, Spec 02 §3, §5.1)."""

from __future__ import annotations

from vigifeu.engine.cluster import cluster_new_hotspots
from vigifeu.engine.fixed_source import (
    confirm_candidate,
    invalidate_candidate,
    list_candidates,
    mark_fixed_sources,
    promote_candidates,
)
from vigifeu.engine.qualify import qualify_events

from .conftest import insert_hotspot

STAMP = "2026-07-27T00:00:00Z"
TORCHE = (43.100, 5.900)   # position type d'une torchère industrielle


def _torche_15_jours(conn, config):
    """Une torchère : même point, FRP faible, 15 jours distincts ⇒ suspect_source_fixe."""
    for jour in range(1, 16):
        insert_hotspot(conn, *TORCHE, f"2026-07-{jour:02d}T12:00:00Z", frp=4.0)
    res = cluster_new_hotspots(conn, config, stamp=STAMP)
    qualify_events(conn, config, res["touched"], stamp=STAMP)
    return next(iter(res["touched"]))


def test_promotion_apres_15_jours(db):
    conn, config = db
    eid = _torche_15_jours(conn, config)
    assert conn.execute(
        "SELECT qualification FROM fire_event WHERE id=?", (eid,)
    ).fetchone()["qualification"] == "suspect_source_fixe"

    created = promote_candidates(conn, config, stamp=STAMP)
    assert len(created) == 1
    src = conn.execute("SELECT * FROM fixed_source WHERE id=?", (created[0],)).fetchone()
    assert src["status"] == "candidat"
    assert abs(src["lat"] - TORCHE[0]) < 0.01


def test_pas_de_promotion_avant_le_seuil(db):
    """Moins de n_promotion_days jours ⇒ pas de candidat."""
    conn, config = db
    for jour in range(1, 6):   # 5 jours seulement
        insert_hotspot(conn, *TORCHE, f"2026-07-{jour:02d}T12:00:00Z", frp=4.0)
    res = cluster_new_hotspots(conn, config, stamp=STAMP)
    qualify_events(conn, config, res["touched"], stamp=STAMP)
    assert promote_candidates(conn, config, stamp=STAMP) == []


def test_pas_de_doublon_de_candidat(db):
    """Deux promotions successives ne créent qu'un candidat pour la même source."""
    conn, config = db
    _torche_15_jours(conn, config)
    promote_candidates(conn, config, stamp=STAMP)
    assert promote_candidates(conn, config, stamp=STAMP) == []
    assert conn.execute("SELECT COUNT(*) AS n FROM fixed_source").fetchone()["n"] == 1


def test_confirmation_et_invalidation(db):
    conn, config = db
    _torche_15_jours(conn, config)
    sid = promote_candidates(conn, config, stamp=STAMP)[0]
    assert len(list_candidates(conn)) == 1

    confirm_candidate(conn, sid, stamp=STAMP, kind="torchère")
    row = conn.execute("SELECT status, kind FROM fixed_source WHERE id=?", (sid,)).fetchone()
    assert row["status"] == "confirme" and row["kind"] == "torchère"
    assert list_candidates(conn) == []

    # Invalidation d'un autre candidat : marqué invalide, jamais supprimé.
    conn.execute("INSERT INTO fixed_source (lat, lon, status) VALUES (44.0, 0.0, 'candidat')")
    other = conn.execute("SELECT id FROM fixed_source WHERE lat=44.0").fetchone()["id"]
    invalidate_candidate(conn, other, stamp=STAMP)
    assert conn.execute(
        "SELECT status FROM fixed_source WHERE id=?", (other,)
    ).fetchone()["status"] == "invalide"


def test_marquage_exclut_du_clustering(db):
    """Après confirmation, un hotspot sur la source est marqué et exclu du clustering."""
    conn, config = db
    _torche_15_jours(conn, config)
    sid = promote_candidates(conn, config, stamp=STAMP)[0]
    confirm_candidate(conn, sid, stamp=STAMP)

    # Nouveau hotspot pile sur la torchère.
    hid = insert_hotspot(conn, *TORCHE, "2026-07-20T12:00:00Z", frp=4.0)
    res_mark = mark_fixed_sources(conn, config)
    assert res_mark["marked"] == 1
    assert conn.execute(
        "SELECT fixed_source_id FROM hotspot_raw WHERE id=?", (hid,)
    ).fetchone()["fixed_source_id"] == sid

    # Le clustering ne le rattache à aucun feu (exclu).
    n_ev_before = conn.execute("SELECT COUNT(*) AS n FROM fire_event").fetchone()["n"]
    cluster_new_hotspots(conn, config, stamp=STAMP)
    assert conn.execute("SELECT fire_event_id FROM hotspot_raw WHERE id=?", (hid,)).fetchone()["fire_event_id"] is None
    assert conn.execute("SELECT COUNT(*) AS n FROM fire_event").fetchone()["n"] == n_ev_before


def test_marquage_sans_source_confirmee_noop(db):
    conn, config = db
    insert_hotspot(conn, *TORCHE, "2026-07-20T12:00:00Z")
    assert mark_fixed_sources(conn, config) == {"marked": 0}
