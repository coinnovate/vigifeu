"""Tests de la qualification des trois signatures (engine/qualify.py, Spec 02 §5)."""

from __future__ import annotations

import json

from vigifeu.engine.cluster import cluster_new_hotspots
from vigifeu.engine.overpass import build_overpasses
from vigifeu.engine.qualify import classify, qualify_events, signature_metrics

from .conftest import insert_hotspot, load_saumos_hotspots

STAMP = "2026-07-27T00:00:00Z"


def _qualify_all(conn, config):
    res = cluster_new_hotspots(conn, config, stamp=STAMP)
    return qualify_events(conn, config, res["touched"], stamp=STAMP)


def _qualification(conn, event_id):
    return conn.execute(
        "SELECT qualification FROM fire_event WHERE id=?", (event_id,)
    ).fetchone()["qualification"]


# ------------------------------------------------------------ classify() pur

def _m(**kw):
    base = {"n_hotspots_dedup": 0, "n_passages": 0, "jours_distincts": 0,
            "emprise_m": 0.0, "frp_median": 0.0, "extension_m": 0.0}
    base.update(kw)
    return base


def test_classify_ordre_r1_avant_r3(db):
    """R1 (source fixe) est évaluée avant R3 : un point fixe persistant et faible,
    même avec plusieurs passages, reste suspect_source_fixe."""
    _, config = db
    m = _m(jours_distincts=5, emprise_m=300, frp_median=4, n_passages=10, n_hotspots_dedup=20)
    assert classify(m, config) == ("suspect_source_fixe", "R1")


def test_classify_r2_isole(db):
    _, config = db
    assert classify(_m(n_passages=1, n_hotspots_dedup=2), config) == ("suspect_isole", "R2")


def test_classify_r3_par_mouvement(db):
    _, config = db
    m = _m(n_passages=2, extension_m=500, n_hotspots_dedup=4)
    assert classify(m, config) == ("vegetation_confirme", "R3")


def test_classify_r3_par_feu_franc(db):
    """8+ pixels sur 2 passages ⇒ vegetation_confirme sans attendre le mouvement."""
    _, config = db
    m = _m(n_passages=2, extension_m=50, n_hotspots_dedup=8)
    assert classify(m, config) == ("vegetation_confirme", "R3")


def test_classify_r4_defaut(db):
    """2 passages, peu de pixels, immobile ⇒ ni R1 ni R3 ⇒ suspect_isole (R4)."""
    _, config = db
    m = _m(n_passages=2, extension_m=100, n_hotspots_dedup=3, jours_distincts=1)
    assert classify(m, config) == ("suspect_isole", "R4")


# ------------------------------------------------------------ scénarios bout-en-bout

def test_source_fixe_r1(db):
    """Même point, FRP faible, 3 jours ⇒ suspect_source_fixe (torchère type)."""
    conn, config = db
    for jour in ("20", "21", "22"):
        insert_hotspot(conn, 43.500, 4.900, f"2026-07-{jour}T12:00:00Z", frp=4.0)
    res = _qualify_all(conn, config)
    eid = next(iter(res["changed"]))
    assert _qualification(conn, eid) == "suspect_source_fixe"


def test_detection_isolee_r2(db):
    """Un seul passage, 2 pixels ⇒ suspect_isole."""
    conn, config = db
    insert_hotspot(conn, 43.500, 4.900, "2026-07-20T12:00:00Z", overpass_id=1)
    insert_hotspot(conn, 43.501, 4.901, "2026-07-20T12:00:00Z", overpass_id=1)
    res = _qualify_all(conn, config)
    eid = next(iter(res["changed"]))
    assert _qualification(conn, eid) == "suspect_isole"


def test_vegetation_r3_par_mouvement(db):
    """Deux passages, front déplacé de ~600 m ⇒ vegetation_confirme."""
    conn, config = db
    insert_hotspot(conn, 44.900, -1.020, "2026-07-22T12:00:00Z", overpass_id=1)
    insert_hotspot(conn, 44.906, -1.020, "2026-07-22T13:40:00Z", overpass_id=2)  # ~670 m N
    res = _qualify_all(conn, config)
    eid = next(iter(res["changed"]))
    assert _qualification(conn, eid) == "vegetation_confirme"


def test_reason_trace_regle_et_hash(db):
    """qualification_reason porte la règle, les mesures et le hash de config."""
    conn, config = db
    for jour in ("20", "21", "22"):
        insert_hotspot(conn, 43.500, 4.900, f"2026-07-{jour}T12:00:00Z", frp=4.0)
    _qualify_all(conn, config)
    reason = conn.execute(
        "SELECT qualification_reason FROM fire_event LIMIT 1"
    ).fetchone()["qualification_reason"]
    data = json.loads(reason)
    assert data["rule"] == "R1"
    assert data["jours_distincts"] == 3
    assert len(data["config"]) == 12


def test_economie_pas_de_requalification_sans_changement(db):
    """Relancer la qualification sans nouveauté ne requalifie rien (§5, économie)."""
    conn, config = db
    for jour in ("20", "21", "22"):
        insert_hotspot(conn, 43.500, 4.900, f"2026-07-{jour}T12:00:00Z", frp=4.0)
    _qualify_all(conn, config)
    again = qualify_events(conn, config, [1], stamp=STAMP)
    assert again["changed"] == set()


def test_saumos_reel_vegetation_confirme(db):
    """Rejeu Gironde ouest : le feu de Saumos ⇒ vegetation_confirme (R3)."""
    conn, config = db
    load_saumos_hotspots(conn, bbox=(44.5, 45.3, -1.30, -0.30))
    build_overpasses(conn, config)
    res = cluster_new_hotspots(conn, config, stamp=STAMP)
    qualify_events(conn, config, res["touched"], stamp=STAMP)

    saumos_id = conn.execute(
        "SELECT fire_event_id FROM hotspot_raw WHERE acq_at='2026-07-22T11:55:00Z' "
        "AND lat BETWEEN 44.88 AND 44.92 AND lon BETWEEN -1.05 AND -0.99 LIMIT 1"
    ).fetchone()["fire_event_id"]
    assert _qualification(conn, saumos_id) == "vegetation_confirme"


def test_saumos_reel_foyer_20juillet_borderline_r3(db):
    """Le foyer du 20/07 (scatter nocturne bref, FRP faible, extension ~430 m juste
    au-dessus d'E_mobile) est aujourd'hui classé R3 par mouvement. Conforme aux
    règles telles que spécifiées, mais c'est le faux positif visé par le point
    ouvert §11.1 (brûlages) : ce test documente le comportement courant — à revoir
    lors du calage saisonnier des seuils (§5.3), pas un invariant du jalon."""
    conn, config = db
    load_saumos_hotspots(conn, bbox=(44.5, 45.3, -1.30, -0.30))
    build_overpasses(conn, config)
    res = cluster_new_hotspots(conn, config, stamp=STAMP)
    qualify_events(conn, config, res["touched"], stamp=STAMP)

    j20_id = conn.execute(
        "SELECT fire_event_id FROM hotspot_raw WHERE acq_at LIKE '2026-07-20%' "
        "AND lat BETWEEN 44.78 AND 44.82 AND lon BETWEEN -1.12 AND -1.08 LIMIT 1"
    ).fetchone()["fire_event_id"]
    m = signature_metrics(conn, j20_id, config)
    # Le foyer reste un événement distinct de Saumos (vérifié dans test_cluster) ;
    # sa qualification est R3 par extension, à la marge du seuil.
    assert m["n_passages"] >= 2 and m["extension_m"] >= config["qualification"]["e_mobile_m"]
    assert _qualification(conn, j20_id) == "vegetation_confirme"
