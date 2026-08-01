"""Relations feu ↔ POI (emprise / a_moins_de_X) + recensement commune ↔ POI (Spec 06 §3).

Scénario synthétique contrôlé (feu piloté par ses cellules, POI ponctuels), miroir de
test_relations.py. Repère à ~45°N : 0,01° lon ≈ 785 m, 0,01° lat ≈ 1112 m.
"""

from __future__ import annotations

from shapely.geometry import box

from vigifeu.engine.relations import (
    compute_poi_relations,
    invalidate_commune_index,
    invalidate_poi_index,
    recompute_commune_poi,
)
from vigifeu.model.db import connect, load_config, migrate

STAMP = "2026-07-22T12:00:00Z"
STAMP2 = "2026-07-24T12:00:00Z"


def _config():
    return load_config()


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


def _poi(conn, lat, lon, *, category="camping", nom="X"):
    conn.execute(
        "INSERT INTO poi (source, source_ref, category, nom, lat, lon, imported_at) "
        "VALUES ('osm', ?, ?, ?, ?, ?, '2026-08-01T00:00:00Z')",
        (f"node/{lat:.5f}:{lon:.5f}", category, nom, lat, lon),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _commune(conn, code, lat0, lat1, lon0, lon1):
    poly = box(lon0, lat0, lon1, lat1)
    conn.execute(
        "INSERT INTO commune (code_insee, slug, nom, geometry_wkt) VALUES (?,?,?,?)",
        (code, f"c-{code}", f"Commune {code}", poly.wkt),
    )


def _db(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    invalidate_poi_index(conn)
    invalidate_commune_index(conn)
    return conn


def test_poi_emprise_et_paliers(tmp_path):
    conn = _db(tmp_path)
    config = _config()
    _fire_with_cell(conn, 45.00, -1.00)
    pin = _poi(conn, 45.00, -1.00)      # dans la cellule → emprise
    pnear = _poi(conn, 45.00, -0.97)    # ~2 km à l'est → a_moins_de_5km
    _poi(conn, 45.00, -0.40)            # ~47 km → aucune relation
    invalidate_poi_index(conn)

    r = compute_poi_relations(conn, config, 1, version_id=None, stamp=STAMP)
    assert r["opened"] == 2

    got = {
        x["poi_id"]: (x["rel_type"], x["distance_km"])
        for x in conn.execute("SELECT poi_id, rel_type, distance_km FROM fe_poi_rel")
    }
    assert got[pin] == ("emprise", None)
    assert got[pnear][0] == "a_moins_de_5km"
    assert 1.0 < got[pnear][1] < 3.0
    assert len(got) == 2  # le POI lointain n'a pas de relation


def test_poi_relation_historisee(tmp_path):
    """Le feu grandit et atteint le POI : a_moins_de_5km fermé, emprise ouvert (jamais supprimé)."""
    conn = _db(tmp_path)
    config = _config()
    _fire_with_cell(conn, 45.00, -1.00)
    pid = _poi(conn, 45.00, -0.97)
    invalidate_poi_index(conn)
    compute_poi_relations(conn, config, 1, version_id=None, stamp=STAMP)

    # Le front avance vers le POI : on ajoute une cellule sur son emplacement.
    _add_cell(conn, 45.00, -0.97)
    compute_poi_relations(conn, config, 1, version_id=None, stamp=STAMP2)

    rows = conn.execute(
        "SELECT rel_type, valid_from, valid_to FROM fe_poi_rel WHERE poi_id=? ORDER BY id", (pid,)
    ).fetchall()
    assert len(rows) == 2
    ferme = next(r for r in rows if r["rel_type"] == "a_moins_de_5km")
    ouvert = next(r for r in rows if r["rel_type"] == "emprise")
    assert ferme["valid_to"] == STAMP2      # ancienne relation fermée, jamais supprimée
    assert ouvert["valid_to"] is None       # nouvelle relation courante


def test_poi_noop_sans_poi(tmp_path):
    """Sans référentiel POI chargé, le calcul est un no-op (cycles Lot 2/3 verts)."""
    conn = _db(tmp_path)
    config = _config()
    _fire_with_cell(conn, 45.00, -1.00)
    invalidate_poi_index(conn)
    r = compute_poi_relations(conn, config, 1, version_id=None, stamp=STAMP)
    assert r == {"opened": 0, "closed": 0, "current": 0, "pois": []}


def test_commune_poi_recensement(tmp_path):
    """recompute_commune_poi rattache chaque POI à la commune qui le contient (point-dans-polygone)."""
    conn = _db(tmp_path)
    _commune(conn, "A", 44.90, 45.10, -1.10, -0.90)   # contient (45.00, -1.00)
    _commune(conn, "B", 44.90, 45.10, 0.50, 0.70)     # ailleurs
    invalidate_commune_index(conn)
    p_in = _poi(conn, 45.00, -1.00)      # dans A
    _poi(conn, 45.00, 5.00)              # hors de toute commune

    res = recompute_commune_poi(conn)
    assert res["pairs"] == 1
    rows = conn.execute("SELECT code_insee, poi_id FROM commune_poi").fetchall()
    assert len(rows) == 1
    assert rows[0]["code_insee"] == "A" and rows[0]["poi_id"] == p_in

    # Idempotent : reconstruit sans doublon.
    assert recompute_commune_poi(conn)["pairs"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM commune_poi").fetchone()["n"] == 1
