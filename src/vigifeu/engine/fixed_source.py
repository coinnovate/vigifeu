"""Registre des sources fixes — marquage et promotion (Spec 02 §3 étape 3, §5.1).

Torchères, aciéries, raffineries : des points qui « brûlent » en permanence sans
être des feux de végétation. Deux mécanismes :

1. **Marquage** (avant clustering) : un hotspot dans le rayon d'une source
   `confirme` reçoit `fixed_source_id` et est exclu du clustering (le clustering
   ne traite que `fixed_source_id IS NULL`).

2. **Promotion** : un FireEvent `suspect_source_fixe` stable sur `n_promotion_days`
   jours distincts devient un **candidat** `fixed_source` (status `candidat`). La
   confirmation reste une revue manuelle (CLI en v1 ; croisement CLC à l'appui,
   Spec 02 §5.1) : `confirm_candidate` / `invalidate_candidate`.

Recalculable (P2) : le marquage se relit depuis les sources confirmées ; la
promotion depuis la qualification. Aucune suppression.
"""

from __future__ import annotations

import json
import math
import sqlite3

from vigifeu.engine import geo
from vigifeu.engine.qualify import signature_metrics


def mark_fixed_sources(conn: sqlite3.Connection, config: dict) -> dict:
    """Marque les hotspots libres tombant dans le rayon d'une source confirmée.

    À exécuter avant le clustering. Retourne {marked}. Idempotent.
    """
    sources = conn.execute(
        "SELECT id, lat, lon, radius_m FROM fixed_source WHERE status='confirme'"
    ).fetchall()
    if not sources:
        return {"marked": 0}

    default_r = config["fixed_source"]["mark_radius_m"]
    src_proj = {s["id"]: geo.project(s["lat"], s["lon"]) for s in sources}
    src_r = {s["id"]: (s["radius_m"] or default_r) for s in sources}

    pending = conn.execute(
        "SELECT id, lat, lon FROM hotspot_raw "
        "WHERE fire_event_id IS NULL AND fixed_source_id IS NULL AND overpass_id IS NOT NULL"
    ).fetchall()
    if not pending:
        return {"marked": 0}
    proj = geo.project_rows([(h["id"], h["lat"], h["lon"]) for h in pending])

    marked = 0
    for h in pending:
        x, y = proj[h["id"]]
        for sid, (sx, sy) in src_proj.items():
            if math.hypot(x - sx, y - sy) <= src_r[sid]:
                conn.execute(
                    "UPDATE hotspot_raw SET fixed_source_id=? WHERE id=?", (sid, h["id"])
                )
                marked += 1
                break
    conn.commit()
    return {"marked": marked}


def promote_candidates(conn: sqlite3.Connection, config: dict, *, stamp: str) -> list[int]:
    """Promeut en candidat fixed_source les suspect_source_fixe stables (§5.1).

    Un feu `suspect_source_fixe` sur ≥ n_promotion_days jours distincts crée un
    candidat, sauf s'il en existe déjà un à proximité. Retourne les ids créés.
    """
    threshold = config["qualification"]["n_promotion_days"]
    min_radius = config["fixed_source"]["candidate_min_radius_m"]

    existing = conn.execute("SELECT lat, lon FROM fixed_source").fetchall()
    existing_proj = [geo.project(s["lat"], s["lon"]) for s in existing]

    created: list[int] = []
    events = conn.execute(
        "SELECT id FROM fire_event WHERE qualification='suspect_source_fixe' "
        "AND lifecycle != 'fusionne'"
    ).fetchall()
    for ev in events:
        metrics = signature_metrics(conn, ev["id"], config)
        if metrics["jours_distincts"] < threshold:
            continue
        hs = conn.execute(
            "SELECT lat, lon, frp_mw FROM hotspot_raw WHERE fire_event_id=?", (ev["id"],)
        ).fetchall()
        lat = sum(h["lat"] for h in hs) / len(hs)
        lon = sum(h["lon"] for h in hs) / len(hs)

        # Déjà une source (candidat ou confirmée) à proximité ⇒ ne pas dupliquer.
        x, y = geo.project(lat, lon)
        if any(math.hypot(x - ex, y - ey) <= min_radius for ex, ey in existing_proj):
            continue

        radius = max(min_radius, metrics["emprise_m"] / 2)
        evidence = json.dumps({
            "from_event": ev["id"],
            "jours_distincts": metrics["jours_distincts"],
            "frp_median": round(metrics["frp_median"], 1),
            "emprise_m": round(metrics["emprise_m"]),
        }, ensure_ascii=False)
        sid = conn.execute(
            "INSERT INTO fixed_source (lat, lon, radius_m, kind, evidence_json, status, first_seen) "
            "VALUES (?, ?, ?, 'inconnu', ?, 'candidat', ?)",
            (lat, lon, radius, evidence, stamp),
        ).lastrowid
        created.append(sid)
        existing_proj.append((x, y))
    conn.commit()
    return created


def list_candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Sources fixes en attente de revue (pour le CLI)."""
    return conn.execute(
        "SELECT id, lat, lon, radius_m, evidence_json, first_seen FROM fixed_source "
        "WHERE status='candidat' ORDER BY id"
    ).fetchall()


def confirm_candidate(conn: sqlite3.Connection, source_id: int, *, stamp: str,
                      kind: str | None = None, clc_code: str | None = None) -> None:
    """Confirme un candidat (revue manuelle, §5.1)."""
    conn.execute(
        "UPDATE fixed_source SET status='confirme', last_review_at=?, "
        "kind=COALESCE(?, kind), clc_code=COALESCE(?, clc_code) WHERE id=?",
        (stamp, kind, clc_code, source_id),
    )
    conn.commit()


def invalidate_candidate(conn: sqlite3.Connection, source_id: int, *, stamp: str) -> None:
    """Rejette un candidat (fausse source fixe) — jamais supprimé, marqué invalide."""
    conn.execute(
        "UPDATE fixed_source SET status='invalide', last_review_at=? WHERE id=?",
        (stamp, source_id),
    )
    conn.commit()
