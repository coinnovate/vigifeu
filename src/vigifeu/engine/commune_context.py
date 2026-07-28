"""Orchestration du contexte communal — drought (EFFIS/Météo des forêts) et vigieau.

Ces fetchers sont **par commune** (Spec 02 §2, quotidien) : on ne les tire que pour
les communes réellement concernées par un feu actif (relation courante), pas toute
la France — la couverture hors-saison des fiches « rien à signaler » relèvera du
générateur (Lot 4).

**Activation derrière flag** (cadrage Lot 3) : les formats d'API EFFIS/Météo des
forêts/VigiEau ne sont pas encore vérifiés (hypothèses documentées, [[lot1-fetchers-externes-hypotheses]]).
L'orchestration est câblée et testée (mocks) ; le flag `activated` de chaque source
(config) reste `false` jusqu'à la passe de vérification live. Flag off ⇒ marche à
blanc (on liste ce qui serait tiré, aucun HTTP). Les fetchers ne lèvent jamais (§9).
"""

from __future__ import annotations

import sqlite3

from vigifeu.ingest.drought import fetch_effis_fwi, fetch_meteo_forets
from vigifeu.ingest.vigieau import fetch_vigieau


def concerned_communes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Communes en relation COURANTE avec un feu actif confirmé (code, dept, centroïde)."""
    return conn.execute(
        "SELECT DISTINCT c.code_insee, c.dept, c.centroid_lat, c.centroid_lon "
        "FROM fe_commune_rel r "
        "JOIN fire_event fe ON fe.id = r.fire_event_id "
        "JOIN commune c ON c.code_insee = r.code_insee "
        "WHERE r.valid_to IS NULL AND fe.lifecycle='actif' "
        "AND fe.qualification='vegetation_confirme'"
    ).fetchall()


def refresh_commune_context(
    conn: sqlite3.Connection,
    config: dict,
    *,
    valid_date: str,
    drought_activated: bool | None = None,
    vigieau_activated: bool | None = None,
) -> dict:
    """Tire drought/vigieau pour les communes concernées, si les flags sont armés.

    `*_activated=None` ⇒ lit le flag `activated` de la config (défaut false). Sinon
    l'override permet aux tests d'armer l'orchestration avec des fetchers mockés.
    Retourne un récapitulatif (communes/depts visés, insertions), même à blanc.
    """
    communes = concerned_communes(conn)
    depts = sorted({c["dept"] for c in communes if c["dept"]})
    d_on = config["drought"].get("activated", False) if drought_activated is None else drought_activated
    v_on = config["vigieau"].get("activated", False) if vigieau_activated is None else vigieau_activated

    res = {
        "communes": len(communes),
        "depts": len(depts),
        "drought_activated": d_on,
        "vigieau_activated": v_on,
        "vigieau_inserted": 0,
        "effis_inserted": 0,
        "meteo_forets_inserted": 0,
    }

    if v_on:
        for c in communes:
            res["vigieau_inserted"] += fetch_vigieau(conn, config, c["code_insee"]).get("inserted", 0)

    if d_on:
        for c in communes:
            if c["centroid_lat"] is None or c["centroid_lon"] is None:
                continue
            res["effis_inserted"] += fetch_effis_fwi(
                conn, config, lat=c["centroid_lat"], lon=c["centroid_lon"],
                valid_date=valid_date, code_insee=c["code_insee"],
            ).get("inserted", 0)
        for dept in depts:
            res["meteo_forets_inserted"] += fetch_meteo_forets(
                conn, config, dept=dept, valid_date=valid_date,
            ).get("inserted", 0)

    return res
