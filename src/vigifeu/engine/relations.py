"""Relations feu ↔ commune (Spec 02 §7, étape 8 du cycle).

Recalculées à chaque nouvelle version d'un feu **publié** (vegetation_confirme).
Trois types : `emprise_dans_commune`, `a_moins_de_{5,10,20}km`, `direction_vent`
(ce dernier dans `wind.py`, recalculé à chaque weather_obs — fait composé).

**Géométrie de référence = union des cellules du feu** (fire_cell_state, grille
`cells.grid_m`), pas le hull convexe : décision de cadrage Lot 3. Le hull d'un
méga-feu ponte les zones non brûlées et surestime l'emprise ; l'union des cellules
épouse la forme réelle. Les cellules sont rafraîchies juste avant chaque version
(process_cycle), donc l'union au moment de la version est le footprint-so-far —
l'historisation (valid_from/valid_to) émerge naturellement à mesure que le front avance.

Intersections et distances en **Lambert-93** (métriques exactes) via un STRtree des
communes en mémoire (~35 000 tiennent en RAM ; plan §1.1). Le STRtree est construit
une fois et caché par connexion, invalidé au changement du référentiel.

Historisation (Spec 01 §5.4) : une relation absente est créée (valid_from) ; une
relation qui cesse est **fermée** (valid_to), jamais supprimée.
"""

from __future__ import annotations

import sqlite3

from shapely import STRtree
from shapely.geometry import Point, box
from shapely.ops import unary_union
from shapely.wkt import loads as wkt_loads

from vigifeu.engine import geo

# Index commune caché par connexion, invalidé si le nombre de communes change
# (le référentiel ne bouge qu'à un import de millésime, rare).
_INDEX_CACHE: dict[int, tuple[int, "CommuneIndex"]] = {}


class CommuneIndex:
    """STRtree des contours communaux en Lambert-93 + accès code_insee ↔ géométrie."""

    def __init__(self, codes: list[str], geoms_l93: list):
        self.codes = codes
        self.geoms = geoms_l93
        self.tree = STRtree(geoms_l93) if geoms_l93 else None

    def __len__(self) -> int:
        return len(self.codes)

    def query(self, geom_l93) -> list[tuple[str, object]]:
        """Communes candidates par recouvrement de bbox (filtre grossier du STRtree)."""
        if self.tree is None:
            return []
        return [(self.codes[i], self.geoms[i]) for i in self.tree.query(geom_l93)]


def build_commune_index(conn: sqlite3.Connection) -> CommuneIndex:
    rows = conn.execute(
        "SELECT code_insee, geometry_wkt FROM commune WHERE geometry_wkt IS NOT NULL"
    ).fetchall()
    codes, geoms = [], []
    for r in rows:
        codes.append(r["code_insee"])
        geoms.append(geo.to_l93_geom(wkt_loads(r["geometry_wkt"])))
    return CommuneIndex(codes, geoms)


def get_commune_index(conn: sqlite3.Connection) -> CommuneIndex:
    count = conn.execute("SELECT COUNT(*) AS n FROM commune").fetchone()["n"]
    cached = _INDEX_CACHE.get(id(conn))
    if cached and cached[0] == count:
        return cached[1]
    idx = build_commune_index(conn)
    _INDEX_CACHE[id(conn)] = (count, idx)
    return idx


def invalidate_commune_index(conn: sqlite3.Connection) -> None:
    _INDEX_CACHE.pop(id(conn), None)


def fire_footprint_l93(conn: sqlite3.Connection, config: dict, fire_event_id: int):
    """Union des cellules du feu (carrés grid_m), en Lambert-93. None si pas de cellule."""
    grid = config["cells"]["grid_m"]
    half = grid / 2.0
    cells = conn.execute(
        "SELECT lat, lon FROM fire_cell_state WHERE fire_event_id=?", (fire_event_id,)
    ).fetchall()
    if not cells:
        return None
    squares = []
    for c in cells:
        x, y = geo.project(c["lat"], c["lon"])
        squares.append(box(x - half, y - half, x + half, y + half))
    return unary_union(squares)


def _palier(distance_km: float, paliers: list[int]) -> int | None:
    for p in paliers:
        if distance_km <= p:
            return p
    return None


def _desired_relations(footprint_l93, index: CommuneIndex, config: dict) -> dict:
    """{code_insee: (rel_type, distance_km|None)} — une relation (la plus forte) par commune."""
    rel = config["relations"]
    paliers = rel["paliers_km"]
    pmax = max(paliers)
    emin_m2 = rel["emprise_min_ha"] * 10_000.0
    desired: dict[str, tuple[str, float | None]] = {}
    if footprint_l93 is None or len(index) == 0:
        return desired
    for code, geom in index.query(footprint_l93.buffer(pmax * 1000)):
        inter = footprint_l93.intersection(geom)
        if not inter.is_empty:
            if inter.area >= emin_m2:
                desired[code] = ("emprise_dans_commune", None)
            # sinon : sliver numérique < emprise_min_ha → ignoré
            continue
        d_km = footprint_l93.distance(geom) / 1000.0
        p = _palier(d_km, paliers)
        if p is not None:
            desired[code] = (f"a_moins_de_{p}km", round(d_km, 2))
    return desired


def _reconcile(conn, fire_event_id, desired, *, version_id, stamp) -> dict:
    """Ouvre les relations nouvelles, ferme celles qui cessent (jamais de suppression).

    Ne touche jamais aux relations `direction_vent` : elles ont leur propre cycle
    (wind.py, hystérésis sur weather_obs).
    """
    open_rows = conn.execute(
        "SELECT id, code_insee, rel_type FROM fe_commune_rel "
        "WHERE fire_event_id=? AND valid_to IS NULL AND rel_type != 'direction_vent'",
        (fire_event_id,),
    ).fetchall()
    open_map = {(r["code_insee"], r["rel_type"]): r["id"] for r in open_rows}
    desired_keys = {(code, rt) for code, (rt, _) in desired.items()}

    touched: set[str] = set()
    closed = 0
    for (code, rt), rid in open_map.items():
        if (code, rt) not in desired_keys:
            conn.execute("UPDATE fe_commune_rel SET valid_to=? WHERE id=?", (stamp, rid))
            closed += 1
            touched.add(code)

    opened = 0
    for code, (rt, dist) in desired.items():
        if (code, rt) not in open_map:
            conn.execute(
                "INSERT INTO fe_commune_rel "
                "(fire_event_id, code_insee, rel_type, distance_km, valid_from, computed_from_version) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (fire_event_id, code, rt, dist, stamp, version_id),
            )
            opened += 1
            touched.add(code)
    conn.commit()
    return {"opened": opened, "closed": closed, "current": len(desired),
            "communes": sorted(touched)}


def compute_commune_relations(
    conn: sqlite3.Connection,
    config: dict,
    fire_event_id: int,
    *,
    version_id: int | None,
    stamp: str,
) -> dict:
    """Recalcule emprise/a_moins_de_X pour un feu et historise (étape 8).

    No-op silencieux si aucune commune n'est chargée (référentiel absent) ou si le
    feu n'a pas de cellules — garde le pipeline Lot 2 vert sans référentiel importé.
    """
    index = get_commune_index(conn)
    footprint = fire_footprint_l93(conn, config, fire_event_id)
    if footprint is None or len(index) == 0:
        return {"opened": 0, "closed": 0, "current": 0, "communes": []}
    desired = _desired_relations(footprint, index, config)
    return _reconcile(conn, fire_event_id, desired, version_id=version_id, stamp=stamp)


# ===========================================================================
# POI / enjeux (Spec 06 §3) — feu ↔ POI (proximité) et commune ↔ POI (point-dans
# -polygone). Réutilise le STRtree et la géométrie Lambert-93 des relations communales :
# un POI est un point, `emprise` = point DANS l'union des cellules (la « zone détectée »).
# ===========================================================================

# Index POI caché par connexion, invalidé si le nombre de POI change (import de référentiel).
_POI_INDEX_CACHE: dict[int, tuple[int, "PoiIndex"]] = {}


class PoiIndex:
    """STRtree des POI (points) en Lambert-93 + accès poi_id ↔ point."""

    def __init__(self, poi_ids: list[int], points_l93: list):
        self.poi_ids = poi_ids
        self.points = points_l93
        self.tree = STRtree(points_l93) if points_l93 else None

    def __len__(self) -> int:
        return len(self.poi_ids)

    def query(self, geom_l93) -> list[tuple[int, object]]:
        if self.tree is None:
            return []
        return [(self.poi_ids[i], self.points[i]) for i in self.tree.query(geom_l93)]


def build_poi_index(conn: sqlite3.Connection) -> PoiIndex:
    ids, pts = [], []
    for r in conn.execute("SELECT id, lat, lon FROM poi").fetchall():
        x, y = geo.project(r["lat"], r["lon"])
        ids.append(r["id"])
        pts.append(Point(x, y))
    return PoiIndex(ids, pts)


def get_poi_index(conn: sqlite3.Connection) -> PoiIndex:
    count = conn.execute("SELECT COUNT(*) AS n FROM poi").fetchone()["n"]
    cached = _POI_INDEX_CACHE.get(id(conn))
    if cached and cached[0] == count:
        return cached[1]
    idx = build_poi_index(conn)
    _POI_INDEX_CACHE[id(conn)] = (count, idx)
    return idx


def invalidate_poi_index(conn: sqlite3.Connection) -> None:
    _POI_INDEX_CACHE.pop(id(conn), None)


def _desired_poi_relations(footprint_l93, index: PoiIndex, config: dict) -> dict:
    """{poi_id: (rel_type, distance_km|None)} — palier le plus fort par POI.

    `emprise` = POI DANS l'union des cellules (la « zone détectée », Spec 06 §4) ;
    sinon `a_moins_de_{p}km` au premier palier atteint (mêmes paliers que les communes).
    """
    paliers = config["relations"]["paliers_km"]
    pmax = max(paliers)
    desired: dict[int, tuple[str, float | None]] = {}
    if footprint_l93 is None or len(index) == 0:
        return desired
    for poi_id, point in index.query(footprint_l93.buffer(pmax * 1000)):
        if footprint_l93.intersects(point):
            desired[poi_id] = ("emprise", None)
            continue
        d_km = footprint_l93.distance(point) / 1000.0
        p = _palier(d_km, paliers)
        if p is not None:
            desired[poi_id] = (f"a_moins_de_{p}km", round(d_km, 2))
    return desired


def _reconcile_poi(conn, fire_event_id, desired, *, version_id, stamp) -> dict:
    """Ouvre les relations nouvelles, ferme celles qui cessent (jamais de suppression)."""
    open_rows = conn.execute(
        "SELECT id, poi_id, rel_type FROM fe_poi_rel WHERE fire_event_id=? AND valid_to IS NULL",
        (fire_event_id,),
    ).fetchall()
    open_map = {(r["poi_id"], r["rel_type"]): r["id"] for r in open_rows}
    desired_keys = {(pid, rt) for pid, (rt, _) in desired.items()}

    touched: set[int] = set()
    closed = 0
    for (pid, rt), rid in open_map.items():
        if (pid, rt) not in desired_keys:
            conn.execute("UPDATE fe_poi_rel SET valid_to=? WHERE id=?", (stamp, rid))
            closed += 1
            touched.add(pid)

    opened = 0
    for pid, (rt, dist) in desired.items():
        if (pid, rt) not in open_map:
            conn.execute(
                "INSERT INTO fe_poi_rel "
                "(fire_event_id, poi_id, rel_type, distance_km, valid_from, computed_from_version) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (fire_event_id, pid, rt, dist, stamp, version_id),
            )
            opened += 1
            touched.add(pid)
    conn.commit()
    return {"opened": opened, "closed": closed, "current": len(desired), "pois": sorted(touched)}


def compute_poi_relations(
    conn: sqlite3.Connection,
    config: dict,
    fire_event_id: int,
    *,
    version_id: int | None,
    stamp: str,
) -> dict:
    """Recalcule emprise/a_moins_de_X pour un feu ↔ POI et historise (Spec 06 §3.1).

    No-op silencieux si aucun POI n'est chargé (garde les cycles sans référentiel POI verts,
    exactement comme les relations communales sans référentiel commune).
    """
    index = get_poi_index(conn)
    footprint = fire_footprint_l93(conn, config, fire_event_id)
    if footprint is None or len(index) == 0:
        return {"opened": 0, "closed": 0, "current": 0, "pois": []}
    desired = _desired_poi_relations(footprint, index, config)
    return _reconcile_poi(conn, fire_event_id, desired, version_id=version_id, stamp=stamp)


def recompute_commune_poi(conn: sqlite3.Connection) -> dict:
    """Recense les POI par commune (point-dans-polygone) → reconstruit `commune_poi` (Spec 06 §3.2).

    Quasi-statique : appelé à l'import d'un référentiel (POI ou communes), pas à chaque cycle.
    Un POI est rattaché à la première commune qui le contient (les communes pavent le plan
    sans recouvrement). No-op si aucune commune chargée.
    """
    cidx = get_commune_index(conn)
    conn.execute("DELETE FROM commune_poi")
    if len(cidx) == 0:
        conn.commit()
        return {"pairs": 0}
    pairs = 0
    for r in conn.execute("SELECT id, lat, lon FROM poi").fetchall():
        x, y = geo.project(r["lat"], r["lon"])
        pt = Point(x, y)
        for code, geom in cidx.query(pt):
            if geom.intersects(pt):
                conn.execute(
                    "INSERT OR IGNORE INTO commune_poi (code_insee, poi_id) VALUES (?, ?)",
                    (code, r["id"]),
                )
                pairs += 1
                break
    conn.commit()
    return {"pairs": pairs}
