"""Jalon L3 — rejeu Saumos, relations feu ↔ commune (plan §2, Spec 02 §10.1).

Rejeu **incrémental** (jour par jour, comme le daemon en production) de l'archive
FIRMS Gironde + le référentiel commune (fixture Gironde-ouest). Le jalon vérifie que
les communes ressortent avec les **bons types de relation** et des **intervalles de
validité cohérents avec la chronologie réelle** : le foyer démarre à Saumos/Le Porge
le 22, le front gagne Lacanau puis le nord les 24–25 (progression ~5,5 km, §10.1).

Hermétique : géométrie + hotspots archivés uniquement, aucun appel réseau (la
relation direction_vent, qui dépend d'Open-Meteo, est testée à part — test_wind.py).
Les relations reposent sur l'union des cellules du feu, pas le hull (cadrage Lot 3).
"""

from __future__ import annotations

import pytest

from vigifeu.engine.overpass import build_overpasses
from vigifeu.engine.pipeline import process_cycle
from vigifeu.engine.relations import invalidate_commune_index
from vigifeu.model.db import connect, load_config, migrate, sync_satellite_sources
from vigifeu.referentiels.communes import import_communes

from .conftest import load_saumos_hotspots

BBOX = (44.5, 45.3, -1.30, -0.30)
COMMUNES = "tests/fixtures/communes/gironde-ouest.geojson"
DAYS = [f"2026-07-{d:02d}" for d in range(20, 28)]

# Communes de référence (codes INSEE réels de Gironde ouest)
SAUMOS, LE_PORGE, LE_TEMPLE = "33503", "33333", "33528"
LACANAU, SAINTE_HELENE, BRACH = "33214", "33417", "33070"


@pytest.fixture(scope="module")
def rejeu(tmp_path_factory):
    """Rejeu incrémental complet une fois pour le module (opération lourde)."""
    path = tmp_path_factory.mktemp("saumos_communes") / "rejeu.db"
    conn = connect(path)
    migrate(conn)
    config = load_config("config/params.toml")
    sync_satellite_sources(conn, config)
    import_communes(conn, COMMUNES, millesime="test-gironde")
    invalidate_commune_index(conn)

    for d in DAYS:
        load_saumos_hotspots(conn, day_prefix=d, bbox=BBOX)
        build_overpasses(conn, config)
        process_cycle(conn, config, stamp=d + "T23:59:00Z")

    saumos_id = conn.execute(
        "SELECT fire_event_id FROM hotspot_raw WHERE acq_at='2026-07-22T11:55:00Z' "
        "AND lat BETWEEN 44.88 AND 44.92 AND lon BETWEEN -1.05 AND -0.99 LIMIT 1"
    ).fetchone()["fire_event_id"]
    yield conn, config, saumos_id
    conn.close()


def _current(conn, saumos_id, rel_like):
    return {
        r["code_insee"]: r
        for r in conn.execute(
            "SELECT code_insee, rel_type, distance_km, valid_from, valid_to "
            "FROM fe_commune_rel WHERE fire_event_id=? AND rel_type LIKE ? AND valid_to IS NULL",
            (saumos_id, rel_like),
        )
    }


def test_feu_reste_le_meme_avec_communes(rejeu):
    """L'import des communes et le rejeu incrémental ne changent pas l'identité du feu."""
    conn, _, saumos_id = rejeu
    fe = conn.execute(
        "SELECT first_acq_at, qualification FROM fire_event WHERE id=?", (saumos_id,)
    ).fetchone()
    assert fe["qualification"] == "vegetation_confirme"
    assert fe["first_acq_at"] == "2026-07-22T11:55:00Z"


def test_communes_emprise_coeur(rejeu):
    """Le cœur du foyer (Saumos, Le Porge, Le Temple) est en emprise dès le 22/07."""
    conn, _, saumos_id = rejeu
    emprise = _current(conn, saumos_id, "emprise_dans_commune")
    assert len(emprise) >= 4  # jalon : « 4+ communes »
    for code in (SAUMOS, LE_PORGE, LE_TEMPLE):
        assert code in emprise, f"{code} attendu en emprise"
        assert emprise[code]["valid_from"].startswith("2026-07-22")


def test_intervalles_suivent_la_chronologie(rejeu):
    """Le front gagne le nord après le foyer : Lacanau et Sainte-Hélène ouvrent
    après le cœur (progression ~5,5 km nord, §10.1)."""
    conn, _, saumos_id = rejeu
    emprise = _current(conn, saumos_id, "emprise_dans_commune")
    coeur = emprise[SAUMOS]["valid_from"]
    assert emprise[LACANAU]["valid_from"] > coeur
    assert emprise[SAINTE_HELENE]["valid_from"] > coeur
    # toutes les ouvertures tombent dans la fenêtre du feu (22–27/07)
    for r in emprise.values():
        assert "2026-07-22" <= r["valid_from"][:10] <= "2026-07-27"


def test_paliers_bien_encodes(rejeu):
    """Chaque a_moins_de_Nkm porte une distance cohérente avec son palier."""
    conn, _, saumos_id = rejeu
    proches = _current(conn, saumos_id, "a_moins_de_%")
    assert proches, "des communes proches sont attendues"
    bornes = {"a_moins_de_5km": (0, 5), "a_moins_de_10km": (5, 10), "a_moins_de_20km": (10, 20)}
    for code, r in proches.items():
        lo, hi = bornes[r["rel_type"]]
        assert lo < r["distance_km"] <= hi, f"{code} {r['rel_type']} d={r['distance_km']}"
    # Brach est un voisin proche (< 5 km) qui n'a pas brûlé
    assert BRACH in proches and proches[BRACH]["rel_type"] == "a_moins_de_5km"


def test_emprise_et_proximite_exclusives(rejeu):
    """Une commune n'a jamais simultanément une emprise et une relation de distance."""
    conn, _, saumos_id = rejeu
    emprise = set(_current(conn, saumos_id, "emprise_dans_commune"))
    proches = set(_current(conn, saumos_id, "a_moins_de_%"))
    assert emprise.isdisjoint(proches)


def test_regen_communes_alimentee(rejeu):
    """Les fiches communes impactées sont bien émises vers regen_queue (§8)."""
    conn, _, _ = rejeu
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM regen_queue WHERE page_type='commune'"
    ).fetchone()["n"]
    assert n >= 4
