"""Tests des primitives géométriques (engine/geo.py).

Vérifie sur des coordonnées réelles (Saumos et l'événement du 20/07) que les
distances Lambert-93, l'enveloppe et l'azimut sont justes — ce sont les briques
sur lesquelles reposent le rattachement (D_link) et les mesures (§6).
"""

from __future__ import annotations

import math

from vigifeu.engine import geo

# Deux points réels de la fixture Saumos.
SAUMOS = (44.90, -1.02)          # départ du feu de Saumos (22/07 12:32Z)
EVT_20JUILLET = (44.80, -1.10)   # foyer distinct du 20/07, au sud-ouest


def test_distance_saumos_vs_20juillet_environ_12km():
    """Les deux foyers sont à ~12,6 km (jalon Spec 02 §10.1 : l'événement du 20/07
    reste distinct de Saumos)."""
    d = geo.distance_m(*SAUMOS, *EVT_20JUILLET)
    assert 11_500 < d < 13_500  # ~12,6 km, distinct au-delà de D_link (1,5 km)


def test_distance_symetrique_et_nulle():
    assert geo.distance_m(*SAUMOS, *SAUMOS) < 1.0
    a = geo.distance_m(*SAUMOS, *EVT_20JUILLET)
    b = geo.distance_m(*EVT_20JUILLET, *SAUMOS)
    assert abs(a - b) < 1.0


def test_distance_courte_sous_seuil_dlink():
    """Deux pixels VIIRS voisins (~300 m) sont bien sous D_link."""
    d = geo.distance_m(44.900, -1.020, 44.902, -1.021)
    assert 100 < d < 500


def test_project_rows_coherent_avec_project():
    rows = [(1, *SAUMOS), (2, *EVT_20JUILLET)]
    projected = geo.project_rows(rows)
    assert set(projected) == {1, 2}
    x, y = geo.project(*SAUMOS)
    assert abs(projected[1][0] - x) < 1e-6
    assert abs(projected[1][1] - y) < 1e-6
    # Distance recalculée depuis les coords projetées = distance_m.
    (x1, y1), (x2, y2) = projected[1], projected[2]
    d_proj = math.hypot(x2 - x1, y2 - y1)
    assert abs(d_proj - geo.distance_m(*SAUMOS, *EVT_20JUILLET)) < 1.0


def test_hull_wkt_nuage_et_degenere():
    assert geo.convex_hull_wkt([]) is None
    assert geo.convex_hull_wkt([(44.9, -1.02)]).startswith("POINT")
    poly = geo.convex_hull_wkt(
        [(44.90, -1.03), (44.91, -1.01), (44.89, -1.01), (44.90, -1.02)]
    )
    assert poly.startswith("POLYGON")


def test_area_ha_carre_connu():
    """Un carré de ~0,01° de côté vers 45°N fait quelques centaines d'hectares."""
    carre = [(44.90, -1.03), (44.91, -1.03), (44.91, -1.02), (44.90, -1.02)]
    a = geo.area_ha(carre)
    # ~1,11 km (lat) × ~0,79 km (lon) ≈ 88 ha.
    assert 60 < a < 120
    assert geo.area_ha([(44.9, -1.02)]) == 0.0


def test_bearing_nord_et_est():
    assert abs(geo.bearing_deg(44.0, 0.0, 45.0, 0.0) - 0.0) < 1.0    # plein nord
    assert abs(geo.bearing_deg(45.0, 0.0, 45.0, 1.0) - 90.0) < 1.0   # plein est
    # Saumos a progressé vers le nord (jalon) : azimut proche de 0/360.
    b = geo.bearing_deg(*EVT_20JUILLET, *SAUMOS)
    assert b < 60 or b > 300
