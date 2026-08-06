"""Amorçage MTG via `geo_candidate` — early-detection (Spec 07 §4.2/§5, étape 6).

Un `geo_candidate` est un **objet interne léger** (jamais un `fire_event`) : un regroupement de
détections MTG persistantes sans confirmation VIIRS. Il porte le suivi interne (base d'une future
notification B2B) ; sa seule trace publique est le carré « signal en attente » (§8), jamais un feu.

`process_candidates` fait trois choses, dans l'ordre, sur l'état courant (idempotent) :
1. **Promotion** — un candidat dont une détection a été confirmée (par `geo_confirm`, étape 5) passe
   `confirme`, reçoit `fire_event_id`, et TOUTES ses détections rejoignent le feu (chronologie).
2. **Amorçage / croissance** — les détections orphelines (non confirmées, hors candidat) récentes sont
   agrégées : on grossit d'abord les candidats `en_attente` proches, puis on crée un candidat pour tout
   nouvel amas atteignant `seed_min_detections` **slots distincts** dans `seed_radius_km`.
3. **Expiration** — un candidat `en_attente` sans détection depuis `[clustering].t_reprise_days` passe
   `expire` (gardé, calibration — destin 3).

À appeler APRÈS `geo_confirm.confirm_detections` dans le cycle (câblage : étapes 8/9).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from vigifeu.engine import geo

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def process_candidates(conn: sqlite3.Connection, config: dict, *, clock: datetime | None = None) -> dict:
    """Promotion + amorçage/croissance + expiration. Retourne {promus, crees, grossis, expires, fires}.

    `fires` = feux dont un candidat vient d'être promu (leur fiche doit être régénérée, étape 9)."""
    now = clock or datetime.now(UTC)
    stats = {"promus": 0, "crees": 0, "grossis": 0, "expires": 0, "fires": []}
    _promote(conn, stats)
    _grow_and_seed(conn, config, now, stats)
    _expire(conn, config, now, stats)
    conn.commit()
    return stats


def _promote(conn: sqlite3.Connection, stats: dict) -> None:
    """Un candidat en attente dont une détection est confirmée VIIRS → confirmé + rattaché au feu."""
    rows = conn.execute(
        "SELECT gc.id AS cand, d.confirmed_by_fire_event_id AS fid, COUNT(*) AS n "
        "FROM geo_candidate gc JOIN geo_detection_raw d ON d.geo_candidate_id = gc.id "
        "WHERE gc.status='en_attente' AND d.confirmed_by_fire_event_id IS NOT NULL "
        "GROUP BY gc.id, d.confirmed_by_fire_event_id "
        "ORDER BY gc.id, n DESC, d.confirmed_by_fire_event_id"  # feu majoritaire, sinon plus petit id
    ).fetchall()
    chosen: dict[int, int] = {}
    for r in rows:
        chosen.setdefault(r["cand"], r["fid"])
    fires: set[int] = set()
    for cand, fid in chosen.items():
        conn.execute("UPDATE geo_candidate SET status='confirme', fire_event_id=? WHERE id=?", (fid, cand))
        conn.execute(
            "UPDATE geo_detection_raw SET confirmed_by_fire_event_id=? "
            "WHERE geo_candidate_id=? AND confirmed_by_fire_event_id IS NULL",
            (fid, cand),
        )
        stats["promus"] += 1
        fires.add(fid)
    stats["fires"] = sorted(fires)


def _grow_and_seed(conn: sqlite3.Connection, config: dict, now: datetime, stats: dict) -> None:
    m = config["mtg"]
    r2 = (m["seed_radius_km"] * 1000.0) ** 2  # rayon² (comparaison euclidienne L93, sans racine)
    min_slots = m["seed_min_detections"]
    horizon = (now - timedelta(hours=m["display_max_h"])).strftime(_ISO)

    orphelines = conn.execute(
        "SELECT id, lat, lon, acq_at FROM geo_detection_raw "
        "WHERE confirmed_by_fire_event_id IS NULL AND geo_candidate_id IS NULL AND acq_at >= ?",
        (horizon,),
    ).fetchall()
    if not orphelines:
        return
    proj = geo.project_rows([(o["id"], o["lat"], o["lon"]) for o in orphelines])
    pool = {o["id"]: o for o in orphelines}

    # 1) grossir les candidats en attente existants (les orphelines proches d'un centroïde le rejoignent).
    for c in conn.execute(
        "SELECT id, centroid_lat, centroid_lon FROM geo_candidate WHERE status='en_attente'"
    ).fetchall():
        cx, cy = geo.project(c["centroid_lat"], c["centroid_lon"])
        pris = [oid for oid in pool if (proj[oid][0] - cx) ** 2 + (proj[oid][1] - cy) ** 2 <= r2]
        if pris:
            conn.executemany(
                "UPDATE geo_detection_raw SET geo_candidate_id=? WHERE id=?",
                [(c["id"], oid) for oid in pris],
            )
            for oid in pris:
                pool.pop(oid)
            _refresh(conn, c["id"])
            stats["grossis"] += 1

    # 2) nouveaux amas parmi les orphelines restantes (groupement glouton autour d'une graine).
    used: set[int] = set()
    for graine in list(pool):
        if graine in used:
            continue
        gx, gy = proj[graine]
        amas = [graine]
        used.add(graine)
        for autre in pool:
            if autre in used:
                continue
            if (proj[autre][0] - gx) ** 2 + (proj[autre][1] - gy) ** 2 <= r2:
                amas.append(autre)
                used.add(autre)
        if len({pool[i]["acq_at"] for i in amas}) >= min_slots:  # slots DISTINCTS (persistance)
            _create(conn, now, [pool[i] for i in amas], stats)
        # sinon : laissées orphelines (montrées comme signal isolé §8, ré-agrégées au prochain cycle)


def _expire(conn: sqlite3.Connection, config: dict, now: datetime, stats: dict) -> None:
    limite = (now - timedelta(days=config["clustering"]["t_reprise_days"])).strftime(_ISO)
    cur = conn.execute(
        "UPDATE geo_candidate SET status='expire' WHERE status='en_attente' AND last_acq_at < ?",
        (limite,),
    )
    stats["expires"] += cur.rowcount


def _create(conn: sqlite3.Connection, now: datetime, dets: list, stats: dict) -> None:
    acqs = [d["acq_at"] for d in dets]
    clat = sum(d["lat"] for d in dets) / len(dets)
    clon = sum(d["lon"] for d in dets) / len(dets)
    cand = conn.execute(
        "INSERT INTO geo_candidate (created_at, first_acq_at, last_acq_at, centroid_lat, "
        "centroid_lon, n_detections, status) VALUES (?, ?, ?, ?, ?, ?, 'en_attente')",
        (now.strftime(_ISO), min(acqs), max(acqs), clat, clon, len(set(acqs))),
    ).lastrowid
    conn.executemany(
        "UPDATE geo_detection_raw SET geo_candidate_id=? WHERE id=?", [(cand, d["id"]) for d in dets]
    )
    stats["crees"] += 1


def _refresh(conn: sqlite3.Connection, cand_id: int) -> None:
    """Recalcule les agrégats d'un candidat depuis ses détections (après croissance)."""
    rows = conn.execute(
        "SELECT lat, lon, acq_at FROM geo_detection_raw WHERE geo_candidate_id=?", (cand_id,)
    ).fetchall()
    if not rows:
        return
    acqs = [r["acq_at"] for r in rows]
    conn.execute(
        "UPDATE geo_candidate SET centroid_lat=?, centroid_lon=?, first_acq_at=?, last_acq_at=?, "
        "n_detections=? WHERE id=?",
        (
            sum(r["lat"] for r in rows) / len(rows),
            sum(r["lon"] for r in rows) / len(rows),
            min(acqs),
            max(acqs),
            len(set(acqs)),
            cand_id,
        ),
    )
