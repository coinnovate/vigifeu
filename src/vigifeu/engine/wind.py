"""Relation `direction_vent` (Spec 02 §7) — fait composé géométrie + vent.

Une commune est en relation `direction_vent` si elle est (partiellement) dans le
secteur angulaire `±a_vent_deg` autour de la **direction vers laquelle le vent
souffle** (aval), à une portée ≤ `d_vent_km` du feu. Recalculée à chaque nouvelle
`weather_obs` (double horodatage à l'affichage — cadrage §4.1).

**Hystérésis** (Spec 02 §7) : ouverture dès la première mesure dans le secteur ;
fermeture seulement après `n_hysteresis` mesures consécutives hors secteur (~45 min)
— sinon un vent oscillant autour de la limite du secteur ferait clignoter la fiche.
L'état est **dérivé de l'historique weather_obs** (pas de colonne compteur) : on
rejoue les N dernières mesures et on ne ferme que si toutes sont hors secteur.

Convention vent : `wind_dir_deg` (météo) = direction D'OÙ vient le vent ; l'aval est
donc `(wind_dir_deg + 180) % 360`. Le secteur est construit comme un coin (wedge)
en Lambert-93 depuis le centroïde de l'empreinte du feu ; l'azimut est mesuré depuis
le nord de la grille L93 (la convergence méridienne < quelques degrés sur la France
est négligeable devant un demi-angle de 30° — v1).
"""

from __future__ import annotations

import math
import sqlite3

from shapely.geometry import Polygon

from vigifeu.engine.relations import fire_footprint_l93, get_commune_index

REL = "direction_vent"


def _sector_l93(origin_xy, downwind_deg, half_angle_deg, radius_m, steps=16) -> Polygon:
    ox, oy = origin_xy
    pts = [(ox, oy)]
    a0, a1 = downwind_deg - half_angle_deg, downwind_deg + half_angle_deg
    for i in range(steps + 1):
        az = math.radians(a0 + (a1 - a0) * i / steps)
        pts.append((ox + radius_m * math.sin(az), oy + radius_m * math.cos(az)))
    return Polygon(pts)


def _in_sector_codes(origin_xy, wind_dir_deg, config, index) -> set[str]:
    rel = config["relations"]
    downwind = (wind_dir_deg + 180.0) % 360.0
    sector = _sector_l93(origin_xy, downwind, rel["a_vent_deg"], rel["d_vent_km"] * 1000.0)
    return {code for code, geom in index.query(sector) if sector.intersects(geom)}


def recompute_direction_vent(
    conn: sqlite3.Connection, config: dict, fire_event_id: int, *, stamp: str
) -> dict:
    """Recalcule les relations direction_vent d'un feu après une nouvelle weather_obs.

    `stamp` = horodatage à porter sur les ouvertures/fermetures (l'observed_at de la
    mesure déclenchante). No-op si pas de commune, pas d'empreinte, ou pas de vent.
    """
    index = get_commune_index(conn)
    footprint = fire_footprint_l93(conn, config, fire_event_id)
    if len(index) == 0 or footprint is None:
        return {"opened": 0, "closed": 0, "current": 0, "communes": []}

    n = config["relations"]["n_hysteresis"]
    recent = conn.execute(
        "SELECT wind_dir_deg FROM weather_obs "
        "WHERE fire_event_id=? AND wind_dir_deg IS NOT NULL "
        "ORDER BY observed_at DESC, id DESC LIMIT ?",
        (fire_event_id, n),
    ).fetchall()
    if not recent:
        return {"opened": 0, "closed": 0, "current": 0, "communes": []}

    centroid = footprint.centroid
    origin = (centroid.x, centroid.y)
    recent_sets = [_in_sector_codes(origin, r["wind_dir_deg"], config, index) for r in recent]
    current_in = recent_sets[0]

    open_rows = conn.execute(
        "SELECT id, code_insee FROM fe_commune_rel "
        "WHERE fire_event_id=? AND rel_type=? AND valid_to IS NULL",
        (fire_event_id, REL),
    ).fetchall()
    open_codes = {r["code_insee"]: r["id"] for r in open_rows}

    opened = closed = 0
    touched: set[str] = set()
    # Ouverture : dès la première mesure dans le secteur.
    for code in current_in:
        if code not in open_codes:
            conn.execute(
                "INSERT INTO fe_commune_rel "
                "(fire_event_id, code_insee, rel_type, valid_from) VALUES (?, ?, ?, ?)",
                (fire_event_id, code, REL, stamp),
            )
            opened += 1
            touched.add(code)
    # Fermeture : seulement après n_hysteresis mesures consécutives hors secteur.
    for code, rid in open_codes.items():
        if code in current_in:
            continue
        if len(recent_sets) >= n and all(code not in s for s in recent_sets):
            conn.execute("UPDATE fe_commune_rel SET valid_to=? WHERE id=?", (stamp, rid))
            closed += 1
            touched.add(code)

    conn.commit()
    return {"opened": opened, "closed": closed, "current": len(current_in),
            "communes": sorted(touched)}
