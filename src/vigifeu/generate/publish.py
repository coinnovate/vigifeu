"""Publication d'un feu : assignation de `public_id` (Spec 01 §3.1, Spec 04 §4).

`public_id` reste NULL tant qu'un feu n'est pas publié (suspects). Le publier, c'est
lui donner une URL stable et définitive : `{annee}-{slug}`, où le lieu principal est
la **commune d'origine** (celle qui contient la première détection). L'identifiant ne
change plus ensuite — une URL publiée ne meurt jamais (Spec 01 P6) ; seul le slug
d'affichage pourrait un jour rediriger, jamais le public_id en base.

Seuls les feux `vegetation_confirme` sont publiés (les suspects n'ont pas de fiche —
Spec 03 §5). Idempotent : un public_id déjà posé n'est jamais réécrit.
"""

from __future__ import annotations

import sqlite3

from shapely import wkt
from shapely.geometry import Point


def origin_commune(conn: sqlite3.Connection, event_id: int):
    """Commune contenant la première détection du feu (lieu principal).

    Repli déterministe si la première détection ne tombe dans aucune commune connue :
    la commune en emprise ouverte le plus tôt, puis le plus petit code INSEE.
    """
    first = conn.execute(
        "SELECT lat, lon FROM hotspot_raw WHERE fire_event_id=? "
        "ORDER BY acq_at, id LIMIT 1",
        (event_id,),
    ).fetchone()
    emprises = conn.execute(
        "SELECT c.code_insee, c.slug, c.nom, c.geometry_wkt, r.valid_from "
        "FROM fe_commune_rel r JOIN commune c ON c.code_insee=r.code_insee "
        "WHERE r.fire_event_id=? AND r.rel_type='emprise_dans_commune' "
        "ORDER BY r.valid_from, c.code_insee",
        (event_id,),
    ).fetchall()
    if not emprises:
        return None
    if first is not None and first["lat"] is not None:
        p = Point(first["lon"], first["lat"])
        for r in emprises:
            if r["geometry_wkt"] and wkt.loads(r["geometry_wkt"]).contains(p):
                return r
    return emprises[0]


def _unique(conn: sqlite3.Connection, base: str, event_id: int) -> str:
    """Rend `base` unique parmi les public_id existants (suffixe -2, -3… si collision)."""
    candidate, n = base, 1
    while True:
        row = conn.execute(
            "SELECT id FROM fire_event WHERE public_id=? AND id<>?", (candidate, event_id)
        ).fetchone()
        if row is None:
            return candidate
        n += 1
        candidate = f"{base}-{n}"


def ensure_public_id(conn: sqlite3.Connection, event_id: int) -> str | None:
    """Assigne (si besoin) et retourne le public_id d'un feu publiable. None sinon."""
    fire = conn.execute(
        "SELECT public_id, qualification, first_acq_at FROM fire_event WHERE id=?",
        (event_id,),
    ).fetchone()
    if fire is None or fire["qualification"] != "vegetation_confirme":
        return None
    if fire["public_id"]:
        return fire["public_id"]
    commune = origin_commune(conn, event_id)
    if commune is None or not fire["first_acq_at"]:
        return None
    annee = fire["first_acq_at"][:4]
    public_id = _unique(conn, f"{annee}-{commune['slug']}", event_id)
    conn.execute("UPDATE fire_event SET public_id=? WHERE id=?", (public_id, event_id))
    conn.commit()
    return public_id
