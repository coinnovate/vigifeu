"""Primitives géométriques du moteur (plan de dev §1.1).

Le stockage des géométries reste en **WGS84 lon/lat** (WKT), cohérent avec
`hotspot_raw.lat/lon` ; les calculs métriques (distances, aires) passent par une
projection **Lambert-93 (EPSG:2154)** — le système légal métropolitain, plan et
isométrique au mètre sur la France, contrairement aux degrés WGS84.

Convention d'axes : toutes les fonctions publiques prennent `lat, lon` (ordre des
observations FIRMS). En interne, shapely/pyproj travaillent en `(x, y) = (lon, lat)`
(`always_xy=True`), jamais exposé au reste du code.

Pour le chemin chaud du clustering (des milliers de comparaisons de distance par
cycle), on projette une fois par cycle via `project_rows` puis on compare en
euclidien sur les coordonnées projetées — exact ET rapide, sans ré-invoquer pyproj
par paire.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Iterable, Sequence

from pyproj import Transformer
from shapely.geometry import MultiPoint

_WGS84 = "EPSG:4326"
_LAMBERT93 = "EPSG:2154"


@lru_cache(maxsize=1)
def _transformer() -> Transformer:
    # always_xy : entrées/sorties en (lon, lat)/(x, y), pas l'ordre géographique.
    return Transformer.from_crs(_WGS84, _LAMBERT93, always_xy=True)


def project(lat: float, lon: float) -> tuple[float, float]:
    """(lat, lon) WGS84 → (x, y) Lambert-93 en mètres."""
    x, y = _transformer().transform(lon, lat)
    return x, y


def project_rows(rows: Iterable[Sequence]) -> dict[int, tuple[float, float]]:
    """Projette en lot des lignes `(id, lat, lon)` → {id: (x, y)}.

    Un seul appel pyproj vectorisé : le hot path du clustering projette tous les
    hotspots du cycle d'un coup, puis compare en euclidien.
    """
    rows = list(rows)
    if not rows:
        return {}
    ids = [r[0] for r in rows]
    lats = [r[1] for r in rows]
    lons = [r[2] for r in rows]
    xs, ys = _transformer().transform(lons, lats)
    return {i: (x, y) for i, x, y in zip(ids, xs, ys)}


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance métrique entre deux points, via Lambert-93."""
    x1, y1 = project(lat1, lon1)
    x2, y2 = project(lat2, lon2)
    return math.hypot(x2 - x1, y2 - y1)


def convex_hull_wkt(points_latlon: Sequence[tuple[float, float]]) -> str | None:
    """Enveloppe convexe d'un nuage `[(lat, lon), …]`, en WKT WGS84 lon/lat.

    Retourne None si le nuage est vide. Un point ⇒ POINT, deux ⇒ LINESTRING,
    trois alignés ⇒ LINESTRING (shapely dégrade naturellement le hull).
    """
    if not points_latlon:
        return None
    mp = MultiPoint([(lon, lat) for lat, lon in points_latlon])
    return mp.convex_hull.wkt


def area_ha(points_latlon: Sequence[tuple[float, float]]) -> float:
    """Aire (hectares) de l'enveloppe convexe, calculée en Lambert-93.

    0.0 tant qu'il n'y a pas de surface (moins de 3 points non alignés) —
    catégorie `estimee` côté modèle (Spec 02 §6).
    """
    if len(points_latlon) < 3:
        return 0.0
    proj = [project(lat, lon) for lat, lon in points_latlon]
    return MultiPoint(proj).convex_hull.area / 10_000.0


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Azimut initial du point 1 vers le point 2, en degrés (0=N, 90=E, 180=S)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def centroid(points_latlon: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """Centroïde (lat, lon) d'un nuage — moyenne simple, suffisante à l'échelle d'un feu."""
    n = len(points_latlon)
    return (sum(p[0] for p in points_latlon) / n, sum(p[1] for p in points_latlon) / n)
