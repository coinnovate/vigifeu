"""Qualification — règles des trois signatures (Spec 02 §5).

Chaque FireEvent est classé d'après son **historique complet**, par ordre
d'évaluation strict (la première règle satisfaite décide) :

  R1  source fixe    (persistant-fixe)  → suspect_source_fixe
  R2  détection isolée (éphémère-unique) → suspect_isole
  R3  végétation     (persistant-mobile) → vegetation_confirme
  R4  par défaut                         → suspect_isole

Tous les comptages portent sur les hotspots **dédupliqués inter-satellites**
(§6) : sans quoi l'ajout d'un satellite dégraderait mécaniquement la
qualification. `qualification_reason` conserve la règle, les valeurs mesurées et
le hash de config — chaque fiche sait pourquoi et avec quels paramètres elle a été
classée (§5.3, explicabilité).

Économie sur les suspects (§5) : ce module ne réécrit `qualification` que si elle
**change** ; il retourne l'ensemble des feux requalifiés, au versionnage (§6) de
n'en tirer une nouvelle version qu'à ce moment-là.
"""

from __future__ import annotations

import json
import math
import sqlite3
from statistics import median

from vigifeu.engine import dedup, geo
from vigifeu.model.db import config_hash

_CONFIRME = "vegetation_confirme"


def signature_metrics(conn: sqlite3.Connection, event_id: int, config: dict) -> dict:
    """Mesure les signatures d'un FireEvent depuis tous ses hotspots (dédupliqués)."""
    hotspots = conn.execute(
        "SELECT id, source_id, lat, lon, acq_at, frp_mw, overpass_id "
        "FROM hotspot_raw WHERE fire_event_id=?",
        (event_id,),
    ).fetchall()
    if not hotspots:
        return {"n_hotspots_dedup": 0, "n_passages": 0, "jours_distincts": 0,
                "emprise_m": 0.0, "frp_median": 0.0, "extension_m": 0.0}

    groups = dedup.dedup_groups(hotspots, config)
    reps = dedup.representative_ids(hotspots, groups)

    n_dedup = dedup.count_dedup(groups)
    jours = len({h["acq_at"][:10] for h in hotspots})
    passages = {h["overpass_id"] for h in hotspots}
    n_passages = len(passages)

    proj = geo.project_rows([(h["id"], h["lat"], h["lon"]) for h in hotspots])
    xs = [proj[h["id"]][0] for h in hotspots]
    ys = [proj[h["id"]][1] for h in hotspots]
    emprise = math.hypot(max(xs) - min(xs), max(ys) - min(ys))  # diagonale d'emprise (L93)

    frps = sorted(h["frp_mw"] for h in hotspots if h["id"] in reps and h["frp_mw"] is not None)
    frp_med = median(frps) if frps else 0.0

    # Extension spatiale entre passages : plus grand déplacement du centroïde d'un
    # passage à l'autre (un feu fixe reste sous le bruit pixel, un feu mobile bouge).
    centroids: dict[int, tuple[float, float, int]] = {}
    for h in hotspots:
        x, y = proj[h["id"]]
        cx, cy, n = centroids.get(h["overpass_id"], (0.0, 0.0, 0))
        centroids[h["overpass_id"]] = (cx + x, cy + y, n + 1)
    pts = [(sx / n, sy / n) for sx, sy, n in centroids.values()]
    extension = 0.0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            extension = max(extension, math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]))

    return {"n_hotspots_dedup": n_dedup, "n_passages": n_passages, "jours_distincts": jours,
            "emprise_m": emprise, "frp_median": frp_med, "extension_m": extension}


def classify(metrics: dict, config: dict) -> tuple[str, str]:
    """Applique R1→R4 dans l'ordre. Retourne (qualification, rule_id)."""
    q = config["qualification"]

    # R1 — source fixe (persistant-fixe)
    if (metrics["jours_distincts"] >= q["jours_distincts_fixe"]
            and metrics["emprise_m"] <= q["e_fixe_m"]
            and metrics["frp_median"] <= q["f_fixe_mw"]):
        return "suspect_source_fixe", "R1"

    # R2 — détection isolée (éphémère-unique)
    if metrics["n_passages"] == 1 and metrics["n_hotspots_dedup"] <= 2:
        return "suspect_isole", "R2"

    # R3 — feu de végétation (persistant-mobile)
    if metrics["n_passages"] >= 2 and (
            metrics["extension_m"] >= q["e_mobile_m"]
            or metrics["n_hotspots_dedup"] >= q["n_franc"]):
        return _CONFIRME, "R3"

    # R4 — par défaut, suspect_isole conservé en observation
    return "suspect_isole", "R4"


def qualify_events(conn: sqlite3.Connection, config: dict, event_ids, *, stamp: str) -> dict:
    """(Re)qualifie les FireEvents touchés. Retourne {changed:set, evaluated:int}.

    N'écrit que si la qualification change (économie de versionnage §5). Les feux
    fusionnés/archivés ne sont pas réévalués.
    """
    cfg_hash = config_hash(config)
    changed: set[int] = set()
    evaluated = 0

    for eid in event_ids:
        fe = conn.execute(
            "SELECT qualification, lifecycle FROM fire_event WHERE id=?", (eid,)
        ).fetchone()
        if fe is None or fe["lifecycle"] == "fusionne":
            continue
        evaluated += 1

        metrics = signature_metrics(conn, eid, config)
        qualification, rule = classify(metrics, config)
        if qualification == fe["qualification"]:
            continue

        reason = json.dumps({
            "rule": rule,
            "n_hotspots_dedup": metrics["n_hotspots_dedup"],
            "n_passages": metrics["n_passages"],
            "jours_distincts": metrics["jours_distincts"],
            "emprise_m": round(metrics["emprise_m"]),
            "frp_median": round(metrics["frp_median"], 1),
            "extension_m": round(metrics["extension_m"]),
            "config": cfg_hash,
            "at": stamp,
        }, ensure_ascii=False)

        # vegetation_confirme = détection VIIRS confirmée (confidence_level, §5.2).
        confidence = "confirme" if qualification == _CONFIRME else None
        conn.execute(
            "UPDATE fire_event SET qualification=?, qualification_reason=?, confidence_level=? "
            "WHERE id=?",
            (qualification, reason, confidence, eid),
        )
        changed.add(eid)

    conn.commit()
    return {"changed": changed, "evaluated": evaluated}
