"""Rejeu Saumos — le test d'intégration canonique du moteur (Spec 02 §10.1).

Le pipeline complet (process_cycle) doit produire, **sans intervention**, depuis
l'archive FIRMS réelle de la Gironde ouest :

  * un FireEvent unique pour le feu de Saumos, vegetation_confirme ;
  * first_acq_at = 2026-07-22 11:55:00Z (plus ancienne détection du cluster,
    passage NOAA-21 ; 12:32Z = première confirmation) ;
  * l'événement du 20/07 à ~12,6 km resté distinct ;
  * une progression du front ~5,5 km vers le nord entre les nuits du 24 et du 25 ;
  * une chute d'intensité nuit/nuit d'un facteur > 10.

Les communes concernées (§10.1) relèvent du Lot 3 (relations feu↔commune).

C'est la pierre de touche de toute évolution du moteur (P2) : chaque changement se
relit contre ce rejeu.
"""

from __future__ import annotations

import pytest

from vigifeu.engine.overpass import build_overpasses
from vigifeu.engine.pipeline import process_cycle, reset_interpretation
from vigifeu.engine.version import front_progress, intensity_series
from vigifeu.model.db import connect, load_config, migrate, sync_satellite_sources

from .conftest import load_saumos_hotspots

STAMP = "2026-07-27T00:00:00Z"
BBOX = (44.5, 45.3, -1.30, -0.30)   # Gironde ouest


@pytest.fixture(scope="module")
def saumos(tmp_path_factory):
    """Rejeu complet une fois pour tout le module (opération lourde ~7000 hotspots)."""
    path = tmp_path_factory.mktemp("saumos") / "rejeu.db"
    conn = connect(path)
    migrate(conn)
    config = load_config("config/params.toml")
    sync_satellite_sources(conn, config)
    load_saumos_hotspots(conn, bbox=BBOX)
    build_overpasses(conn, config)
    process_cycle(conn, config, stamp=STAMP)

    saumos_id = conn.execute(
        "SELECT fire_event_id FROM hotspot_raw WHERE acq_at='2026-07-22T11:55:00Z' "
        "AND lat BETWEEN 44.88 AND 44.92 AND lon BETWEEN -1.05 AND -0.99 LIMIT 1"
    ).fetchone()["fire_event_id"]
    yield conn, config, saumos_id
    conn.close()


def test_saumos_evenement_unique(saumos):
    """Le cœur du 22/07 (11:55 et 12:32) appartient à un seul et même FireEvent."""
    conn, _, saumos_id = saumos
    ids = {
        r["fire_event_id"]
        for r in conn.execute(
            "SELECT DISTINCT fire_event_id FROM hotspot_raw "
            "WHERE acq_at IN ('2026-07-22T11:55:00Z', '2026-07-22T12:32:00Z') "
            "AND lat BETWEEN 44.88 AND 44.92 AND lon BETWEEN -1.05 AND -0.99"
        )
    }
    assert ids == {saumos_id}


def test_first_acq_11h55(saumos):
    """first_acq_at contractuel = première détection du cluster (NOAA-21, 11:55Z)."""
    conn, _, saumos_id = saumos
    fe = conn.execute("SELECT first_acq_at FROM fire_event WHERE id=?", (saumos_id,)).fetchone()
    assert fe["first_acq_at"] == "2026-07-22T11:55:00Z"


def test_saumos_vegetation_confirme(saumos):
    conn, _, saumos_id = saumos
    fe = conn.execute(
        "SELECT qualification, confidence_level FROM fire_event WHERE id=?", (saumos_id,)
    ).fetchone()
    assert fe["qualification"] == "vegetation_confirme"
    assert fe["confidence_level"] == "confirme"
    # Un feu confirmé est versionné (relecture de propagation).
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM fire_event_version WHERE fire_event_id=?", (saumos_id,)
    ).fetchone()["n"] >= 1


def test_foyer_20juillet_distinct(saumos):
    """Le foyer du 20/07 (~12,6 km) est un événement distinct de Saumos (§10.1)."""
    conn, _, saumos_id = saumos
    j20 = conn.execute(
        "SELECT DISTINCT fire_event_id FROM hotspot_raw WHERE acq_at LIKE '2026-07-20%' "
        "AND lat BETWEEN 44.78 AND 44.82 AND lon BETWEEN -1.12 AND -1.08 "
        "AND fire_event_id IS NOT NULL"
    ).fetchall()
    assert j20, "foyer du 20/07 non rattaché"
    assert saumos_id not in {r["fire_event_id"] for r in j20}


def test_progression_nord_nuit_24_vers_25(saumos):
    """Le front progresse ~5,5 km vers le nord entre les nuits du 24 et du 25."""
    conn, _, saumos_id = saumos
    passage = conn.execute(
        "SELECT id FROM overpass WHERE day_night='N' AND window_start LIKE '2026-07-25%' "
        "ORDER BY window_start LIMIT 1"
    ).fetchone()["id"]
    prog = front_progress(conn, saumos_id, passage)
    assert 4.5 < prog["north_km"] < 7.0
    assert prog["bearing"] < 90 or prog["bearing"] > 270     # vers le nord


def test_chute_intensite_nuit_sur_nuit(saumos):
    """L'intensité nuit/nuit chute d'un facteur > 10 sur la durée du feu."""
    conn, config, saumos_id = saumos
    nuits = [s["frp"] for s in intensity_series(conn, saumos_id, config)
             if s["dn"] == "N" and s["frp"] > 0]
    assert max(nuits) > 10 * nuits[-1]


def test_rejeu_reproductible(saumos):
    """P2 : reset + rejeu redonne le même nombre de feux, confirmés et membership."""
    conn, config, _ = saumos

    def snap():
        return (
            conn.execute("SELECT COUNT(*) AS n FROM fire_event").fetchone()["n"],
            conn.execute("SELECT COUNT(*) AS n FROM fire_event WHERE qualification='vegetation_confirme'").fetchone()["n"],
            conn.execute("SELECT COUNT(*) AS n FROM hotspot_raw WHERE fire_event_id IS NOT NULL").fetchone()["n"],
        )

    before = snap()
    reset_interpretation(conn, config)
    process_cycle(conn, config, stamp=STAMP)
    assert snap() == before
