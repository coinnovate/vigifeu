"""Lectures socle pour le canal contributif (Spec 10 §4, étape 3).

Deux besoins, servis en **lecture seule** sur la socle (`connect_socle_readonly`, cf. db.py) :

- **`feux_proches`** — l'ancre du dépôt est la **géoloc live** ; on remonte les `fire_event`
  **publiés** dont un `hotspot_raw` tombe à moins de `rayon_max_km`, triés par distance
  (le plus proche d'abord). Aucun → refus explicite côté endpoint (§0/§4) ;
- **`commune_du_point`** — commune **contenant le hotspot** (point-dans-polygone) → `code_insee`,
  lien optionnel du widget (§7.4).

Les distances passent par `engine.geo` (Lambert-93, exact au mètre) ; la containment par
shapely sur les contours communaux reprojetés — mêmes primitives que le moteur, jamais de
géométrie ad hoc.
"""

from __future__ import annotations

import math
import sqlite3

from shapely.geometry import Point
from shapely.wkt import loads as wkt_loads

from vigifeu.engine import geo

# Marge du préfiltre commune : superset large autour du point (la containment exacte
# tranche ensuite). 0.5° ≈ 55 km — bien au-delà du rayon d'une commune métropolitaine,
# donc jamais de faux négatif dû au préfiltre.
_MARGE_COMMUNE_DEG = 0.5


def _bbox_deg(lat: float, lon: float, rayon_km: float) -> tuple[float, float, float, float]:
    """Fenêtre lat/lon (degrés) englobant un disque de `rayon_km` — préfiltre SQL grossier."""
    dlat = rayon_km / 111.0
    dlon = rayon_km / (111.0 * max(math.cos(math.radians(lat)), 0.1))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def feux_proches(
    conn: sqlite3.Connection, lat: float, lon: float, rayon_max_km: float
) -> list[dict]:
    """`fire_event` publiés ayant un hotspot < `rayon_max_km` de (lat, lon), triés par distance.

    Chaque entrée retient le **hotspot le plus proche** du feu (ancre géométrique du dépôt) :
    `{fire_event_id, public_id, hotspot_raw_id, distance_km}`. Liste vide = aucun feu proche
    (le endpoint en fait un refus, §4). Préfiltre bbox en SQL, distance exacte en Lambert-93.
    """
    lat_min, lat_max, lon_min, lon_max = _bbox_deg(lat, lon, rayon_max_km)
    rows = conn.execute(
        "SELECT fe.id AS fire_event_id, fe.public_id, "
        "       h.id AS hotspot_raw_id, h.lat, h.lon "
        "FROM hotspot_raw h "
        "JOIN fe_hotspot fh ON fh.hotspot_id = h.id "
        "JOIN fire_event_version fev ON fev.id = fh.fire_event_version_id "
        "JOIN fire_event fe ON fe.id = fev.fire_event_id "
        "WHERE h.lat BETWEEN ? AND ? AND h.lon BETWEEN ? AND ? "
        "AND fe.public_id IS NOT NULL AND fe.lifecycle != 'fusionne'",
        (lat_min, lat_max, lon_min, lon_max),
    ).fetchall()

    best: dict[int, dict] = {}
    for r in rows:
        d_km = geo.distance_m(lat, lon, r["lat"], r["lon"]) / 1000.0
        if d_km > rayon_max_km:
            continue  # coin de la bbox hors du disque
        actuel = best.get(r["fire_event_id"])
        if actuel is None or d_km < actuel["distance_km"]:
            best[r["fire_event_id"]] = {
                "fire_event_id": r["fire_event_id"],
                "public_id": r["public_id"],
                "hotspot_raw_id": r["hotspot_raw_id"],
                "distance_km": round(d_km, 3),
            }
    return sorted(best.values(), key=lambda e: e["distance_km"])


def commune_du_point(conn: sqlite3.Connection, lat: float, lon: float) -> str | None:
    """`code_insee` de la commune contenant (lat, lon), sinon None (offshore/hors couverture).

    Préfiltre par centroïde (fenêtre large) pour ne reprojeter que quelques contours, puis
    containment exacte (`covers` = intérieur + frontière). Les communes pavent le plan sans
    recouvrement → la première qui contient le point est la bonne.
    """
    dlat = _MARGE_COMMUNE_DEG
    dlon = _MARGE_COMMUNE_DEG / max(math.cos(math.radians(lat)), 0.1)
    rows = conn.execute(
        "SELECT code_insee, geometry_wkt FROM commune "
        "WHERE geometry_wkt IS NOT NULL "
        "AND centroid_lat BETWEEN ? AND ? AND centroid_lon BETWEEN ? AND ?",
        (lat - dlat, lat + dlat, lon - dlon, lon + dlon),
    ).fetchall()
    if not rows:
        return None

    pt = Point(*geo.project(lat, lon))
    for r in rows:
        contour = geo.to_l93_geom(wkt_loads(r["geometry_wkt"]))
        if contour.covers(pt):
            return r["code_insee"]
    return None
