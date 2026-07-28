"""Relations feu ↔ commune : emprise / a_moins_de_X + historisation (Lot 3, L3.3).

Scénario synthétique contrôlé (communes carrées, feu piloté par ses cellules) pour
tester la logique d'intersection, les paliers, et l'ouverture/fermeture historisée
sans dépendre de la fixture Saumos.

Repère : à la latitude ~45°N, 0,01° de longitude ≈ 785 m, 0,01° de latitude ≈ 1112 m.
On place des communes de 0,2° de côté (~17–22 km) bien séparées pour des paliers nets.
"""

from __future__ import annotations

from shapely.geometry import box

from vigifeu.engine import geo
from vigifeu.engine.relations import (
    compute_commune_relations,
    fire_footprint_l93,
    invalidate_commune_index,
)


def _commune(conn, code, lat0, lat1, lon0, lon1):
    poly = box(lon0, lat0, lon1, lat1)  # WGS84 lon/lat
    conn.execute(
        "INSERT INTO commune (code_insee, slug, nom, geometry_wkt) VALUES (?,?,?,?)",
        (code, f"c-{code}", f"Commune {code}", poly.wkt),
    )


def _fire_with_cell(conn, lat, lon, *, event_id=1):
    conn.execute(
        "INSERT INTO fire_event (id, created_at, qualification, lifecycle) "
        "VALUES (?, '2026-07-22T00:00:00Z', 'vegetation_confirme', 'actif')",
        (event_id,),
    )
    _add_cell(conn, lat, lon, event_id=event_id)
    return event_id


def _add_cell(conn, lat, lon, *, event_id=1):
    conn.execute(
        "INSERT INTO fire_cell_state (fire_event_id, cell_key, lat, lon) VALUES (?,?,?,?)",
        (event_id, f"{lat:.4f}:{lon:.4f}", lat, lon),
    )


def test_emprise_dans_commune(db):
    conn, config = db
    invalidate_commune_index(conn)
    # Commune A contient le feu ; commune B est à l'écart (> 20 km).
    _commune(conn, "A", 44.90, 45.10, -1.10, -0.90)
    _commune(conn, "B", 44.90, 45.10, 0.50, 0.70)
    _fire_with_cell(conn, 45.00, -1.00)
    r = compute_commune_relations(conn, config, 1, version_id=None, stamp="2026-07-22T12:00:00Z")
    assert r["opened"] == 1
    rel = conn.execute(
        "SELECT code_insee, rel_type FROM fe_commune_rel WHERE valid_to IS NULL"
    ).fetchall()
    assert {(x["code_insee"], x["rel_type"]) for x in rel} == {("A", "emprise_dans_commune")}


def test_a_moins_de_paliers(db):
    conn, config = db
    invalidate_commune_index(conn)
    _fire_with_cell(conn, 45.00, -1.00)  # cellule ~750 m autour de ce point
    # Commune contenant le feu
    _commune(conn, "IN", 44.95, 45.05, -1.05, -0.95)
    # Bord ouest à ~2,8 km à l'est du feu (palier 5)
    _commune(conn, "P5", 44.95, 45.05, -0.96, -0.92)
    # Bord ouest à ~7,5 km à l'est du feu (palier 10)
    _commune(conn, "P10", 44.95, 45.05, -0.90, -0.84)
    # Commune au-delà de 20 km : aucune relation
    _commune(conn, "FAR", 44.95, 45.05, -0.40, -0.30)
    compute_commune_relations(conn, config, 1, version_id=None, stamp="2026-07-22T12:00:00Z")
    got = {
        x["code_insee"]: (x["rel_type"], x["distance_km"])
        for x in conn.execute("SELECT code_insee, rel_type, distance_km FROM fe_commune_rel")
    }
    assert got["IN"][0] == "emprise_dans_commune"
    assert got["P5"][0] == "a_moins_de_5km"
    assert got["P10"][0] == "a_moins_de_10km"
    assert "FAR" not in got
    # distances croissantes et cohérentes
    assert 0 < got["P5"][1] <= 5
    assert 5 < got["P10"][1] <= 10


def test_historisation_ouverture_fermeture(db):
    """Le front grandit : une commune passe de a_moins_de à emprise ; l'ancienne
    relation est fermée (valid_to), la nouvelle ouverte — jamais de suppression."""
    conn, config = db
    invalidate_commune_index(conn)
    _commune(conn, "A", 44.95, 45.05, -1.05, -0.95)   # feu dedans
    _commune(conn, "B", 44.95, 45.05, -0.96, -0.86)   # d'abord à ~2,8 km (palier 5)
    _fire_with_cell(conn, 45.00, -1.00)
    compute_commune_relations(conn, config, 1, version_id=None, stamp="2026-07-22T12:00:00Z")
    b1 = conn.execute(
        "SELECT rel_type, valid_from, valid_to FROM fe_commune_rel WHERE code_insee='B'"
    ).fetchone()
    assert b1["rel_type"] == "a_moins_de_5km" and b1["valid_to"] is None

    # Le feu s'étend vers l'est, jusque dans B.
    _add_cell(conn, 45.00, -0.93)
    _add_cell(conn, 45.00, -0.91)
    compute_commune_relations(conn, config, 1, version_id=None, stamp="2026-07-24T12:00:00Z")

    rows = conn.execute(
        "SELECT rel_type, valid_from, valid_to FROM fe_commune_rel WHERE code_insee='B' ORDER BY id"
    ).fetchall()
    assert len(rows) == 2  # l'ancienne fermée + la nouvelle ouverte, rien de supprimé
    closed = [r for r in rows if r["valid_to"] is not None]
    current = [r for r in rows if r["valid_to"] is None]
    assert closed[0]["rel_type"] == "a_moins_de_5km"
    assert closed[0]["valid_to"] == "2026-07-24T12:00:00Z"
    assert current[0]["rel_type"] == "emprise_dans_commune"
    assert current[0]["valid_from"] == "2026-07-24T12:00:00Z"


def test_footprint_union_cellules(db):
    """L'empreinte est bien l'union des cellules (deux cellules disjointes → deux carrés)."""
    conn, config = db
    _fire_with_cell(conn, 45.00, -1.00)
    _add_cell(conn, 45.00, -0.50)  # ~39 km à l'est : union non convexe, deux composantes
    fp = fire_footprint_l93(conn, config, 1)
    assert fp.geom_type in ("MultiPolygon", "GeometryCollection")
    # aire ≈ 2 carrés de 750 m (≈ 2 × 56 ha = 112 ha), pas le pont convexe entre eux
    assert fp.area / 1e4 < 200


def test_noop_sans_commune(db):
    """Sans référentiel commune chargé, le calcul est un no-op (pipeline Lot 2 vert)."""
    conn, config = db
    invalidate_commune_index(conn)
    _fire_with_cell(conn, 45.00, -1.00)
    r = compute_commune_relations(conn, config, 1, version_id=None, stamp="2026-07-22T12:00:00Z")
    assert r == {"opened": 0, "closed": 0, "current": 0, "communes": []}
