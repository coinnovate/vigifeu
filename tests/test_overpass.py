"""Tests de la construction des passages (Spec 01 §3.2, Spec 02 §3 étape 2).

Vérifie sur la fixture Saumos réelle que les passages regroupent correctement
les granules d'un même satellite et séparent les passages successifs.
"""

from __future__ import annotations

from vigifeu.engine.overpass import build_overpasses, rebuild_overpasses

from .conftest import load_saumos_hotspots


def test_passage_saumos_snpp_22juillet(db):
    """Le passage SNPP du 22/07 midi (détection Saumos) regroupe 12:29 et 12:32,
    et reste distinct des passages voisins (01h, 02h, 14h)."""
    conn, config = db
    load_saumos_hotspots(conn, day_prefix="2026-07-22", sources={"VIIRS_SNPP_NRT"})
    build_overpasses(conn, config)

    # Le passage couvrant 12:29-12:32.
    ov = conn.execute(
        "SELECT * FROM overpass WHERE window_start <= '2026-07-22T12:32:00Z' "
        "AND window_end >= '2026-07-22T12:29:00Z'"
    ).fetchall()
    assert len(ov) == 1
    passage = ov[0]
    assert passage["window_start"] == "2026-07-22T12:29:00Z"
    assert passage["window_end"] == "2026-07-22T12:32:00Z"
    assert passage["day_night"] == "D"

    # Les 4 passages SNPP du jour (01h, 02h45, 12h30, 14h) sont distincts.
    n_passages = conn.execute(
        "SELECT COUNT(*) AS n FROM overpass o "
        "JOIN satellite_source s ON s.id = o.source_id "
        "WHERE s.code='VIIRS_SNPP_NRT'"
    ).fetchone()["n"]
    assert n_passages == 4


def test_tous_les_hotspots_rattaches(db):
    """Après construction, aucun hotspot ne reste sans passage."""
    conn, config = db
    load_saumos_hotspots(conn, day_prefix="2026-07-22")
    res = build_overpasses(conn, config)

    total = conn.execute("SELECT COUNT(*) AS n FROM hotspot_raw").fetchone()["n"]
    orphelins = conn.execute(
        "SELECT COUNT(*) AS n FROM hotspot_raw WHERE overpass_id IS NULL"
    ).fetchone()["n"]
    assert orphelins == 0
    assert res["n_attached"] == total


def test_effectif_coherent(db):
    """La somme des n_hotspots des passages égale le nombre de hotspots."""
    conn, config = db
    load_saumos_hotspots(conn, day_prefix="2026-07-22")
    build_overpasses(conn, config)

    total = conn.execute("SELECT COUNT(*) AS n FROM hotspot_raw").fetchone()["n"]
    somme = conn.execute("SELECT COALESCE(SUM(n_hotspots),0) AS s FROM overpass").fetchone()["s"]
    assert somme == total


def test_passage_ne_melange_pas_les_satellites(db):
    """Un passage n'agrège que les hotspots d'un seul satellite (§3.2)."""
    conn, config = db
    load_saumos_hotspots(conn, day_prefix="2026-07-22")
    build_overpasses(conn, config)

    for ov in conn.execute("SELECT id FROM overpass"):
        n_src = conn.execute(
            "SELECT COUNT(DISTINCT source_id) AS n FROM hotspot_raw WHERE overpass_id=?",
            (ov["id"],),
        ).fetchone()["n"]
        assert n_src == 1


def test_incrementalite_et_idempotence(db):
    """Rattachement incrémental : deux appels successifs ne dupliquent rien ;
    un second lot de hotspots est rattaché sans retoucher le premier."""
    conn, config = db

    load_saumos_hotspots(conn, day_prefix="2026-07-22", sources={"VIIRS_SNPP_NRT"})
    r1 = build_overpasses(conn, config)
    n_ov_1 = conn.execute("SELECT COUNT(*) AS n FROM overpass").fetchone()["n"]

    # Relancer sans nouveauté = no-op.
    r2 = build_overpasses(conn, config)
    assert r2["n_attached"] == 0
    assert r2["n_new_overpasses"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM overpass").fetchone()["n"] == n_ov_1

    # Ajouter un autre satellite : nouveaux passages, les anciens intacts.
    load_saumos_hotspots(conn, day_prefix="2026-07-22", sources={"VIIRS_NOAA20_NRT"})
    r3 = build_overpasses(conn, config)
    assert r3["n_attached"] > 0
    assert r3["n_new_overpasses"] > 0
    assert conn.execute("SELECT COUNT(*) AS n FROM overpass").fetchone()["n"] > n_ov_1
    # Le passage midi SNPP reste identique.
    assert r1["n_new_overpasses"] == 4


def test_rebuild_reproductible(db):
    """Le recalcul complet (P2) reproduit exactement le même découpage."""
    conn, config = db
    load_saumos_hotspots(conn, day_prefix="2026-07-22")
    build_overpasses(conn, config)

    avant = conn.execute(
        "SELECT source_id, window_start, window_end, n_hotspots FROM overpass "
        "ORDER BY source_id, window_start"
    ).fetchall()
    avant = [tuple(r) for r in avant]

    rebuild_overpasses(conn, config)
    apres = conn.execute(
        "SELECT source_id, window_start, window_end, n_hotspots FROM overpass "
        "ORDER BY source_id, window_start"
    ).fetchall()
    apres = [tuple(r) for r in apres]

    assert avant == apres
    # Et toujours zéro orphelin après rebuild.
    orphelins = conn.execute(
        "SELECT COUNT(*) AS n FROM hotspot_raw WHERE overpass_id IS NULL"
    ).fetchone()["n"]
    assert orphelins == 0
