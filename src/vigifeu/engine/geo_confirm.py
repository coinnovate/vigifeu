"""Rattachement / confirmation des détections MTG aux feux VIIRS (Spec 07 §5, étape 5).

Une détection `geo_detection_raw` est **confirmée** quand elle tombe dans la fenêtre spatio-temporelle
d'un feu VIIRS (`confirm_radius_km` autour de l'empreinte du feu, `confirm_window_h` autour de sa période
d'activité). On pose alors `confirmed_by_fire_event_id`. Elle rejoint la chronologie haute fréquence du
feu (frise de tendance, étape 7).

**Bidirectionnel par construction.** La fonction balaie l'ÉTAT COURANT (détections non confirmées ×
feux actifs), donc le même balayage couvre les deux sens :
- *VIIRS puis MTG* : une détection récente proche d'un feu déjà là est rattachée ;
- *MTG puis VIIRS* (early-detection) : une détection ANTÉRIEURE au feu est happée quand le feu VIIRS
  apparaît (sa période ± `confirm_window_h` couvre l'instant de la détection).

À appeler après le cycle d'ingestion MTG ET après le clustering VIIRS (câblage : étapes 8/9). Idempotent
(une détection déjà confirmée n'est pas re-balayée). Ne mélange JAMAIS les FRP (§6) : ne touche que le lien.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from shapely import STRtree
from shapely.geometry import Point

from vigifeu.engine import geo
from vigifeu.engine.relations import fire_footprint_l93


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def confirm_detections(
    conn: sqlite3.Connection, config: dict, *, clock: datetime | None = None
) -> dict:
    """Rattache les détections MTG non confirmées & récentes aux feux VIIRS proches. Idempotent.

    Retourne {n_confirmed, fires} (fires = ids touchés, pour enfiler leur régén — étape 7).
    """
    m = config["mtg"]
    radius_m = m["confirm_radius_km"] * 1000.0
    window = timedelta(hours=m["confirm_window_h"])
    now = clock or datetime.now(UTC)
    # Borne de récence : on ne re-balaie pas indéfiniment les détections « jamais confirmées »
    # (destin 3, calibration). Au-delà de 2× la fenêtre, une détection a déjà eu sa chance.
    horizon = (now - 2 * window).strftime("%Y-%m-%dT%H:%M:%SZ")

    dets = conn.execute(
        "SELECT id, lat, lon, acq_at FROM geo_detection_raw "
        "WHERE confirmed_by_fire_event_id IS NULL AND acq_at >= ?",
        (horizon,),
    ).fetchall()
    if not dets:
        return {"n_confirmed": 0, "fires": []}

    fires = conn.execute(
        "SELECT id, first_acq_at, last_acq_at FROM fire_event "
        "WHERE merged_into IS NULL AND lifecycle IN ('actif', 'plus_detecte')"
    ).fetchall()
    footprints: list = []
    meta: list[tuple[int, datetime, datetime]] = []
    for f in fires:
        fp = fire_footprint_l93(conn, config, f["id"])
        t0, t1 = _parse(f["first_acq_at"]), _parse(f["last_acq_at"])
        if fp is None or t0 is None or t1 is None:
            continue
        footprints.append(fp)
        meta.append((f["id"], t0, t1))
    if not footprints:
        return {"n_confirmed": 0, "fires": []}
    tree = STRtree(footprints)

    updates: list[tuple[int, int]] = []
    touched: set[int] = set()
    for d in dets:
        t = _parse(d["acq_at"])
        if t is None:
            continue
        x, y = geo.project(d["lat"], d["lon"])
        pt = Point(x, y)
        best: tuple[int, float] | None = None
        for i in tree.query(pt.buffer(radius_m)):
            fid, t0, t1 = meta[i]
            if not (t0 - window <= t <= t1 + window):
                continue
            dist = footprints[i].distance(pt)
            if dist > radius_m:
                continue
            if best is None or dist < best[1]:
                best = (fid, dist)
        if best is not None:
            updates.append((best[0], d["id"]))
            touched.add(best[0])

    for fid, det_id in updates:
        conn.execute(
            "UPDATE geo_detection_raw SET confirmed_by_fire_event_id=? WHERE id=?", (fid, det_id)
        )
    conn.commit()
    return {"n_confirmed": len(updates), "fires": sorted(touched)}
