"""Cycle de vie spatial d'un feu — fire_cell_state (Spec 01 §4.3, Spec 02 §7).

Chaque feu est découpé en cellules de grille (~750 m, projection Lambert-93).
Une cellule agrège first/last acquisition et FRP max de ses hotspots, et porte un
**état courant** (choix v1, plan §1.2) lu par rapport à la dernière activité du
feu :

  front_actif   vue dans le dernier groupe de passages (bord d'attaque) ;
  recent        vue dans les dernières `t_recent_h` ;
  plus_detecte  au-delà (le feu a quitté cette cellule).

L'état courant est **recalculable** intégralement depuis hotspot_raw (P1/P2) :
`rebuild_cells` efface puis réécrit les cellules d'un feu — idempotent. C'est aussi
le support du `front_progress` des versions (§6) : la date de première détection
par cellule situe le bord d'attaque d'un passage à l'autre.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta

from vigifeu.engine import geo

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _parse(iso: str) -> datetime:
    return datetime.strptime(iso, _ISO)


def cell_key(x: float, y: float, grid_m: float) -> str:
    """Indice de cellule (colonne_ligne) pour un point projeté L93."""
    return f"{math.floor(x / grid_m)}_{math.floor(y / grid_m)}"


def rebuild_cells(conn: sqlite3.Connection, config: dict, event_id: int,
                  *, clock: str | None = None) -> dict:
    """Recalcule fire_cell_state d'un feu depuis ses hotspots. Retourne les compteurs
    d'état {front_actif, recent, plus_detecte}."""
    grid_m = config["cells"]["grid_m"]
    t_front = timedelta(hours=config["cells"]["t_front_h"])
    t_recent = timedelta(hours=config["cells"]["t_recent_h"])

    hotspots = conn.execute(
        "SELECT id, lat, lon, acq_at, frp_mw FROM hotspot_raw WHERE fire_event_id=?",
        (event_id,),
    ).fetchall()

    conn.execute("DELETE FROM fire_cell_state WHERE fire_event_id=?", (event_id,))
    if not hotspots:
        conn.commit()
        return {"front_actif": 0, "recent": 0, "plus_detecte": 0}

    proj = geo.project_rows([(h["id"], h["lat"], h["lon"]) for h in hotspots])

    # Horloge de référence : dernière détection du feu (état courant).
    clock_dt = _parse(clock) if clock else max(_parse(h["acq_at"]) for h in hotspots)

    cells: dict[str, dict] = {}
    for h in hotspots:
        x, y = proj[h["id"]]
        key = cell_key(x, y, grid_m)
        t = _parse(h["acq_at"])
        frp = h["frp_mw"] if h["frp_mw"] is not None else 0.0
        c = cells.get(key)
        if c is None:
            cells[key] = {"first": t, "last": t, "frp_max": frp,
                          "slat": h["lat"], "slon": h["lon"], "n": 1}
        else:
            c["first"] = min(c["first"], t)
            c["last"] = max(c["last"], t)
            c["frp_max"] = max(c["frp_max"], frp)
            c["slat"] += h["lat"]
            c["slon"] += h["lon"]
            c["n"] += 1

    counts = {"front_actif": 0, "recent": 0, "plus_detecte": 0}
    for key, c in cells.items():
        silence = clock_dt - c["last"]
        if silence <= t_front:
            state = "front_actif"
        elif silence <= t_recent:
            state = "recent"
        else:
            state = "plus_detecte"
        counts[state] += 1
        # lat/lon de la cellule = centroïde de ses hotspots (moyenne en degrés,
        # suffisante à l'échelle 750 m — pas de reprojection inverse coûteuse).
        conn.execute(
            "INSERT INTO fire_cell_state "
            "(fire_event_id, cell_key, lat, lon, first_acq_at, last_acq_at, frp_max_mw, state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, key, c["slat"] / c["n"], c["slon"] / c["n"],
             c["first"].strftime(_ISO), c["last"].strftime(_ISO), c["frp_max"], state),
        )
    conn.commit()
    return counts
