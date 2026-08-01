"""GeoJSON par page (Spec 04 §2, Spec 03 §3.3/§3.9).

Le GeoJSON est **pré-généré** par le serveur (pas construit en JS) : la carte MapLibre
ne fait que l'afficher, et la légende contractuelle vit dans le gabarit, pas dans le JS.
Le GeoJSON est aussi l'export « données brutes » (§3.9) et l'amorce de l'API.
"""

from __future__ import annotations

import sqlite3

from shapely import wkt
from shapely.geometry import Point, box, mapping

from vigifeu.engine import geo
from vigifeu.lexique import fr


def _cell_square(lat: float, lon: float, grid_m: float):
    """Carré de cellule (grille ~750 m) en WGS84, projeté proprement via Lambert-93."""
    x, y = geo.project(lat, lon)
    h = grid_m / 2.0
    return geo.to_wgs84_geom(box(x - h, y - h, x + h, y + h))


def _poi_features(conn, event_id):
    """Marqueurs POI (Spec 06 §4/§7) : paliers emprise + < 5 km courants uniquement.

    Catégorie + palier en propriétés ; JAMAIS de nom ni de capacité (public agrégé, P0).
    Les paliers 10/20 km ne vont pas sur la carte (décision Spec 06 §4 : anti-encombrement)."""
    feats = []
    for r in conn.execute(
        "SELECT p.lat, p.lon, p.category, r.rel_type "
        "FROM fe_poi_rel r JOIN poi p ON p.id = r.poi_id "
        "WHERE r.fire_event_id=? AND r.valid_to IS NULL "
        "AND r.rel_type IN ('emprise', 'a_moins_de_5km')",
        (event_id,),
    ):
        feats.append({
            "type": "Feature",
            "properties": {
                "couche": "poi",
                "category": r["category"],
                "tier": "emprise" if r["rel_type"] == "emprise" else "proche",
                "libelle": fr.libelle_categorie_poi(r["category"]),
            },
            "geometry": mapping(Point(r["lon"], r["lat"])),
        })
    return feats


def feu_geojson(conn: sqlite3.Connection, config: dict, event_id: int) -> dict:
    """FeatureCollection d'un feu : cellules colorées par ancienneté + enveloppe + POI (enjeux)."""
    grid = config["cells"]["grid_m"]
    t_recent = config["cells"]["t_recent_h"]
    features = []
    for c in conn.execute(
        "SELECT lat, lon, state, last_acq_at, frp_max_mw FROM fire_cell_state "
        "WHERE fire_event_id=? AND lat IS NOT NULL AND lon IS NOT NULL",
        (event_id,),
    ):
        libelle = fr.libelle_zone_cellule(c["state"], t_recent_h=t_recent) if c["state"] else None
        features.append({
            "type": "Feature",
            "properties": {"couche": "cellule", "state": c["state"], "libelle": libelle},
            "geometry": mapping(_cell_square(c["lat"], c["lon"], grid)),
        })
    lv = conn.execute(
        "SELECT geometry_wkt FROM fire_event_version WHERE fire_event_id=? "
        "ORDER BY version_n DESC LIMIT 1",
        (event_id,),
    ).fetchone()
    if lv and lv["geometry_wkt"]:
        features.append({
            "type": "Feature",
            "properties": {"couche": "enveloppe"},
            "geometry": mapping(wkt.loads(lv["geometry_wkt"])),
        })
    features.extend(_poi_features(conn, event_id))
    return {"type": "FeatureCollection", "features": features}


def national_geojson(conn: sqlite3.Connection, config: dict) -> dict:
    """FeatureCollection des feux publiés non archivés — marqueurs (centroïde de l'enveloppe)."""
    features = []
    rows = conn.execute(
        "SELECT f.id, f.public_id, f.lifecycle, f.confidence_level "
        "FROM fire_event f WHERE f.public_id IS NOT NULL AND f.lifecycle <> 'archive'"
    ).fetchall()
    for r in rows:
        lv = conn.execute(
            "SELECT geometry_wkt FROM fire_event_version WHERE fire_event_id=? "
            "ORDER BY version_n DESC LIMIT 1",
            (r["id"],),
        ).fetchone()
        if not lv or not lv["geometry_wkt"]:
            continue
        c = wkt.loads(lv["geometry_wkt"]).centroid
        lieu = r["public_id"].partition("-")[2].replace("-", " ").title()
        features.append({
            "type": "Feature",
            "properties": {
                "public_id": r["public_id"],
                "nom": f"Feu de {lieu}",
                "lifecycle": r["lifecycle"],
                "confidence": r["confidence_level"],
                "url": f"/feux/{r['public_id']}/",
            },
            "geometry": mapping(Point(c.x, c.y)),
        })
    return {"type": "FeatureCollection", "features": features}
