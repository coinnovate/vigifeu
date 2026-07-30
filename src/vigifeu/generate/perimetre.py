"""Périmètre d'indexation par vagues (Spec 04 §5).

Source unique de vérité : quelles communes ont une page générée = quelles communes
sont listées au sitemap = quelles communes sont liées depuis les pages département.
Les trois DOIVENT être alignés, sinon le sitemap pointe vers des 404 (SEO).

Montée progressive (Spec 04 §5) : la vague courante = communes **concernées** par un feu
suivi (`fe_commune_rel`, toujours incluses) OU à **historique BDIFF significatif**
(≥ `wave_min_history_fires` feux recensés). On élargit une vague en abaissant le seuil en
config (puis `vigifeu rebuild`), quand Search Console montre une indexation saine.
"""

from __future__ import annotations

import sqlite3


def communes_indexables(conn: sqlite3.Connection, config: dict) -> list[sqlite3.Row]:
    """Communes de la vague courante (code_insee, slug, nom, dept), triées dept puis nom."""
    seuil = config["generate"].get("wave_min_history_fires", 3)
    return conn.execute(
        "SELECT c.code_insee, c.slug, c.nom, c.dept FROM commune c WHERE "
        "  c.code_insee IN (SELECT code_insee FROM fe_commune_rel) "
        "  OR c.code_insee IN (SELECT code_insee FROM commune_fire_history "
        "                      GROUP BY code_insee HAVING COUNT(*) >= ?) "
        "ORDER BY c.dept, c.nom",
        (seuil,),
    ).fetchall()


def depts_indexables(conn: sqlite3.Connection, config: dict) -> list[str]:
    """Départements ayant au moins une commune dans la vague courante."""
    return sorted({r["dept"] for r in communes_indexables(conn, config) if r["dept"]})
