"""Tests de la déduplication inter-satellites (engine/dedup.py, Spec 02 §6)."""

from __future__ import annotations

from vigifeu.engine.dedup import count_dedup, dedup_groups, representative_ids
from vigifeu.model.db import load_config

CONFIG = load_config("config/params.toml")


def _hs(id, source_id, lat, lon, acq_at, frp=10.0):
    return {"id": id, "source_id": source_id, "lat": lat, "lon": lon,
            "acq_at": acq_at, "frp_mw": frp}


def test_deux_satellites_meme_point_fusionnent():
    """Même point physique vu par SNPP puis NOAA-20 à 12 min ⇒ un seul groupe."""
    hs = [
        _hs(1, 10, 44.900, -1.020, "2026-07-22T12:32:00Z", frp=58.0),
        _hs(2, 11, 44.900, -1.020, "2026-07-22T12:44:00Z", frp=42.0),
    ]
    groups = dedup_groups(hs, CONFIG)
    assert groups[1] == groups[2]
    assert count_dedup(groups) == 1
    # Le représentant est le FRP le plus fort (58 MW, id 1).
    assert representative_ids(hs, groups) == {1}


def test_meme_satellite_jamais_dedupe():
    """Deux pixels adjacents du MÊME satellite restent distincts (§6 : inter-sat)."""
    hs = [
        _hs(1, 10, 44.900, -1.020, "2026-07-22T12:32:00Z"),
        _hs(2, 10, 44.901, -1.021, "2026-07-22T12:32:00Z"),
    ]
    groups = dedup_groups(hs, CONFIG)
    assert groups[1] != groups[2]
    assert count_dedup(groups) == 2


def test_trop_loin_dans_le_temps_non_dedupe():
    """Deux satellites au même endroit mais à 25 min ⇒ passages distincts."""
    hs = [
        _hs(1, 10, 44.900, -1.020, "2026-07-22T12:32:00Z"),
        _hs(2, 11, 44.900, -1.020, "2026-07-22T12:57:00Z"),
    ]
    assert count_dedup(dedup_groups(hs, CONFIG)) == 2


def test_trop_loin_dans_l_espace_non_dedupe():
    """Deux satellites simultanés mais à ~800 m (> 375 m) ⇒ points distincts."""
    hs = [
        _hs(1, 10, 44.900, -1.020, "2026-07-22T12:32:00Z"),
        _hs(2, 11, 44.907, -1.020, "2026-07-22T12:35:00Z"),
    ]
    assert count_dedup(dedup_groups(hs, CONFIG)) == 2


def test_transitivite_trois_satellites():
    """SNPP relie NOAA-20 et NOAA-21 vus chacun à < 20 min : un seul point physique."""
    hs = [
        _hs(1, 11, 44.900, -1.020, "2026-07-22T12:25:00Z"),  # NOAA-20
        _hs(2, 10, 44.900, -1.020, "2026-07-22T12:32:00Z"),  # SNPP (pivot)
        _hs(3, 12, 44.900, -1.020, "2026-07-22T12:40:00Z"),  # NOAA-21
    ]
    groups = dedup_groups(hs, CONFIG)
    assert groups[1] == groups[2] == groups[3]
    assert count_dedup(groups) == 1


def test_groupe_stable_sur_plus_petit_id():
    hs = [
        _hs(5, 11, 44.900, -1.020, "2026-07-22T12:44:00Z"),
        _hs(3, 10, 44.900, -1.020, "2026-07-22T12:32:00Z"),
    ]
    groups = dedup_groups(hs, CONFIG)
    assert groups[3] == groups[5] == "g3"


def test_nuage_vide():
    assert dedup_groups([], CONFIG) == {}
    assert count_dedup({}) == 0
