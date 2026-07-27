"""Construction des passages satellite (Spec 01 §3.2, Spec 02 §3 étape 2).

Un `overpass` regroupe les hotspots d'un même satellite dont les acquisitions
tombent dans une même fenêtre temporelle (`overpass.window_min`, ±). C'est un
objet **dérivé et recalculable** (P2) : il ne porte aucune donnée nouvelle, il
organise `hotspot_raw` pour permettre les comparaisons entre passages
comparables (FRP nuit/nuit), la déduplication inter-satellites et la notion de
« détecté au dernier passage ».

Le rattachement est **incrémental** : chaque cycle ne traite que les hotspots
non encore rattachés (`overpass_id IS NULL`), sans recalcul global. La fonction
`rebuild_overpasses` permet le recalcul complet (recalculabilité P2) — utile si
la fenêtre de config change.

overpass_id est un attribut d'interprétation, pas un fait observé : le poser ou
le réinitialiser ne viole pas l'immuabilité des observations (P1).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _parse(iso: str) -> datetime:
    return datetime.strptime(iso, _ISO)


def build_overpasses(conn: sqlite3.Connection, config: dict) -> dict:
    """Rattache les hotspots non rattachés à un passage (en crée au besoin).

    Retourne {n_new_overpasses, n_attached, n_touched}. Idempotent : sans hotspot
    à rattacher, ne fait rien.
    """
    window = timedelta(minutes=config["overpass"]["window_min"])

    sources = [
        r["source_id"]
        for r in conn.execute(
            "SELECT DISTINCT source_id FROM hotspot_raw WHERE overpass_id IS NULL"
        )
    ]

    n_new = 0
    n_attached = 0
    touched: set[int] = set()

    for source_id in sources:
        # Passages existants de ce satellite (enveloppe temporelle courante).
        existing = [
            {
                "id": r["id"],
                "start": _parse(r["window_start"]),
                "end": _parse(r["window_end"]),
            }
            for r in conn.execute(
                "SELECT id, window_start, window_end FROM overpass "
                "WHERE source_id=? ORDER BY window_start",
                (source_id,),
            )
        ]

        rows = conn.execute(
            "SELECT id, acq_at FROM hotspot_raw "
            "WHERE overpass_id IS NULL AND source_id=? ORDER BY acq_at, id",
            (source_id,),
        ).fetchall()

        for hs in rows:
            t = _parse(hs["acq_at"])
            match = next(
                (
                    ov
                    for ov in existing
                    if ov["start"] - window <= t <= ov["end"] + window
                ),
                None,
            )
            if match is None:
                ov_id = conn.execute(
                    "INSERT INTO overpass (source_id, window_start, window_end, n_hotspots) "
                    "VALUES (?, ?, ?, 0)",
                    (source_id, hs["acq_at"], hs["acq_at"]),
                ).lastrowid
                match = {"id": ov_id, "start": t, "end": t}
                existing.append(match)
                existing.sort(key=lambda o: o["start"])
                n_new += 1
            else:
                # Étend l'enveloppe du passage (chaînage des granules successifs).
                match["start"] = min(match["start"], t)
                match["end"] = max(match["end"], t)

            conn.execute(
                "UPDATE hotspot_raw SET overpass_id=? WHERE id=?", (match["id"], hs["id"])
            )
            n_attached += 1
            touched.add(match["id"])

    for ov_id in touched:
        _refresh_overpass(conn, ov_id)

    conn.commit()
    return {"n_new_overpasses": n_new, "n_attached": n_attached, "n_touched": len(touched)}


def _refresh_overpass(conn: sqlite3.Connection, overpass_id: int) -> None:
    """Recalcule fenêtre, effectif et jour/nuit d'un passage depuis ses membres."""
    agg = conn.execute(
        "SELECT MIN(acq_at) AS a, MAX(acq_at) AS b, COUNT(*) AS n "
        "FROM hotspot_raw WHERE overpass_id=?",
        (overpass_id,),
    ).fetchone()
    # jour/nuit : la classe dominante des hotspots du passage (D ou N, unanime en
    # pratique pour un passage VIIRS au-dessus de la France).
    dn = conn.execute(
        "SELECT day_night FROM hotspot_raw "
        "WHERE overpass_id=? AND day_night IS NOT NULL "
        "GROUP BY day_night ORDER BY COUNT(*) DESC LIMIT 1",
        (overpass_id,),
    ).fetchone()
    conn.execute(
        "UPDATE overpass SET window_start=?, window_end=?, n_hotspots=?, day_night=? WHERE id=?",
        (agg["a"], agg["b"], agg["n"], dn["day_night"] if dn else None, overpass_id),
    )


def rebuild_overpasses(conn: sqlite3.Connection, config: dict) -> dict:
    """Recalcul complet (P2) : détache tout, vide overpass, reconstruit.

    overpass_id étant dérivé, sa réinitialisation ne touche pas aux observations.
    """
    conn.execute("UPDATE hotspot_raw SET overpass_id=NULL")
    conn.execute("DELETE FROM overpass")
    conn.commit()
    return build_overpasses(conn, config)
