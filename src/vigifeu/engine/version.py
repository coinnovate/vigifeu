"""Versions de FireEvent et mesures factuelles (Spec 01 §4.2, Spec 02 §6).

Une `fire_event_version` fige un état successif du feu : géométrie (enveloppe),
comptages dédupliqués, FRP du dernier passage, aire estimée, et les mesures de
propagation. Ces mesures ne se comparent qu'entre **passages comparables** (même
jour/nuit — sensibilité capteur différente, cadrage §7ter) :

* `frp_total_last_pass_mw` : somme des FRP du dernier passage, dédupliquée ;
* série d'intensité nuit/nuit et jour/jour (dans `stats_json`) ;
* `front_progress_km` / `front_bearing_deg` : déplacement du centroïde des
  détections entre le dernier passage et le passage comparable précédent (même
  jour/nuit, ~un cycle nuit→nuit ou jour→jour d'écart) ;
* `area_ha_estimee` : enveloppe × facteur de remplissage (catégorie estimee).

Note (§6) : le « bord d'attaque » a plusieurs estimateurs possibles (centroïde des
détections, centroïde des cellules neuves, front nord…), qui divergent de
plusieurs km sur un grand feu. On retient le déplacement du **centroïde des
détections d'un passage à l'autre** : robuste, peu bruité, et cohérent avec le
jalon (Saumos, nuit 24→25 : ~5,9 km nord). Affiné au calage saisonnier si besoin.
"""

from __future__ import annotations

import json
import math
import sqlite3
from statistics import median

from vigifeu.engine import dedup, geo
from vigifeu.model.db import config_hash


def _passages(conn: sqlite3.Connection, event_id: int) -> list[sqlite3.Row]:
    """Passages du feu (overpass distincts), ordonnés par fenêtre."""
    return conn.execute(
        "SELECT DISTINCT o.id, o.window_start, o.window_end, o.day_night "
        "FROM overpass o JOIN hotspot_raw h ON h.overpass_id=o.id "
        "WHERE h.fire_event_id=? ORDER BY o.window_start",
        (event_id,),
    ).fetchall()


def _passage_dedup(conn: sqlite3.Connection, event_id: int, passage_id: int,
                   config: dict) -> tuple[float, int]:
    """FRP total et nombre de hotspots d'un passage, dédupliqués inter-satellites (§6)."""
    hs = conn.execute(
        "SELECT id, source_id, lat, lon, acq_at, frp_mw FROM hotspot_raw "
        "WHERE fire_event_id=? AND overpass_id=?",
        (event_id, passage_id),
    ).fetchall()
    if not hs:
        return 0.0, 0
    groups = dedup.dedup_groups(hs, config)
    reps = dedup.representative_ids(hs, groups)
    frp = sum(h["frp_mw"] for h in hs if h["id"] in reps and h["frp_mw"] is not None)
    return frp, len(reps)


def _passage_frp_dedup(conn: sqlite3.Connection, event_id: int, passage_id: int,
                       config: dict) -> float:
    """Somme des FRP d'un passage, dédupliquée inter-satellites (§6)."""
    return _passage_dedup(conn, event_id, passage_id, config)[0]


def intensity_series(conn: sqlite3.Connection, event_id: int, config: dict) -> list[dict]:
    """Série par passage {at (window_start), dn (day_night), frp, n_dedup} — §3.5/§3.6."""
    series = []
    for p in _passages(conn, event_id):
        frp, n = _passage_dedup(conn, event_id, p["id"], config)
        series.append({
            "at": p["window_start"],
            "dn": p["day_night"],
            "frp": round(frp),
            "n_dedup": n,
        })
    return series


def _passage_centroid(conn: sqlite3.Connection, event_id: int, passage_id: int):
    r = conn.execute(
        "SELECT AVG(lat) AS la, AVG(lon) AS lo, COUNT(*) AS n FROM hotspot_raw "
        "WHERE fire_event_id=? AND overpass_id=?",
        (event_id, passage_id),
    ).fetchone()
    return (r["la"], r["lo"]) if r["n"] else None


def front_progress(conn: sqlite3.Connection, event_id: int, passage_id: int) -> dict:
    """Progression à `passage_id` vs le passage comparable précédent (même jour/nuit).

    Déplacement du centroïde des détections d'un passage à l'autre. Retourne
    {km, bearing, north_km} ; km=0 s'il n'existe pas de passage comparable antérieur.
    """
    p = conn.execute(
        "SELECT window_start, day_night FROM overpass WHERE id=?", (passage_id,)
    ).fetchone()
    prev = conn.execute(
        "SELECT o.id FROM overpass o JOIN hotspot_raw h ON h.overpass_id=o.id "
        "WHERE h.fire_event_id=? AND o.day_night=? AND o.window_start < ? "
        "GROUP BY o.id ORDER BY o.window_start DESC LIMIT 1",
        (event_id, p["day_night"], p["window_start"]),
    ).fetchone()
    if prev is None:
        return {"km": 0.0, "bearing": None, "north_km": 0.0}

    front = _passage_centroid(conn, event_id, passage_id)
    ref = _passage_centroid(conn, event_id, prev["id"])
    if front is None or ref is None:
        return {"km": 0.0, "bearing": None, "north_km": 0.0}

    km = geo.distance_m(*ref, *front) / 1000.0
    bearing = geo.bearing_deg(*ref, *front)
    north_km = km * math.cos(math.radians(bearing))
    return {"km": km, "bearing": bearing, "north_km": north_km}


def create_version(conn: sqlite3.Connection, config: dict, event_id: int, *,
                   stamp: str, trigger_run_id: int | None = None,
                   reprise: bool = False) -> int:
    """Crée une nouvelle fire_event_version pour le feu et retourne son id.

    Suppose fire_cell_state à jour. Écrit aussi fe_hotspot (lien version ↔ hotspot
    avec dedup_group) pour la relecture historique.
    """
    hotspots = conn.execute(
        "SELECT id, source_id, lat, lon, acq_at, frp_mw, overpass_id "
        "FROM hotspot_raw WHERE fire_event_id=?",
        (event_id,),
    ).fetchall()

    groups = dedup.dedup_groups(hotspots, config)
    n_dedup = dedup.count_dedup(groups)
    points = [(h["lat"], h["lon"]) for h in hotspots]
    hull = geo.convex_hull_wkt(points)
    area = geo.area_ha(points) * config["version"]["area_fill_factor"]

    passages = _passages(conn, event_id)
    last_passage = passages[-1] if passages else None
    frp_last = (_passage_frp_dedup(conn, event_id, last_passage["id"], config)
                if last_passage else 0.0)
    prog = front_progress(conn, event_id, last_passage["id"]) if last_passage else \
        {"km": 0.0, "bearing": None, "north_km": 0.0}

    version_n = (conn.execute(
        "SELECT COALESCE(MAX(version_n), 0) AS v FROM fire_event_version WHERE fire_event_id=?",
        (event_id,),
    ).fetchone()["v"]) + 1

    stats = {
        "intensity": intensity_series(conn, event_id, config),
        "reprise": reprise,
        "config": config_hash(config),
    }

    version_id = conn.execute(
        "INSERT INTO fire_event_version "
        "(fire_event_id, version_n, computed_at, trigger_ingestion_run_id, geometry_wkt, "
        " n_hotspots, n_hotspots_dedup, frp_total_last_pass_mw, area_ha_estimee, "
        " front_progress_km, front_bearing_deg, stats_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, version_n, stamp, trigger_run_id, hull, len(hotspots), n_dedup,
         round(frp_last, 1), round(area, 1),
         round(prog["km"], 2), round(prog["bearing"], 1) if prog["bearing"] is not None else None,
         json.dumps(stats, ensure_ascii=False)),
    ).lastrowid

    conn.executemany(
        "INSERT INTO fe_hotspot (fire_event_version_id, hotspot_id, dedup_group) VALUES (?, ?, ?)",
        [(version_id, h["id"], groups[h["id"]]) for h in hotspots],
    )
    conn.commit()
    return version_id
