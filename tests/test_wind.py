"""Relation direction_vent + hystérésis (Lot 3, L3.4).

Vent synthétique, communes carrées, feu piloté par une cellule — hors rejeu Saumos
(qui n'a pas de weather_obs). Vérifie : ouverture dès la 1re mesure dans le secteur ;
fermeture seulement après n_hysteresis mesures consécutives hors secteur.

Convention : wind_dir_deg = direction D'OÙ vient le vent. Vent d'ouest (270°) →
souffle vers l'est (aval 90°) → la commune EST entre dans le secteur.
"""

from __future__ import annotations

from shapely.geometry import box

from vigifeu.engine.relations import invalidate_commune_index
from vigifeu.engine.wind import recompute_direction_vent

REL = "direction_vent"


def _commune(conn, code, lat0, lat1, lon0, lon1):
    conn.execute(
        "INSERT INTO commune (code_insee, slug, nom, geometry_wkt) VALUES (?,?,?,?)",
        (code, f"c-{code}", code, box(lon0, lat0, lon1, lat1).wkt),
    )


def _fire(conn, lat, lon, event_id=1):
    conn.execute(
        "INSERT INTO fire_event (id, created_at, qualification, lifecycle) "
        "VALUES (?, '2026-07-22T00:00:00Z', 'vegetation_confirme', 'actif')",
        (event_id,),
    )
    conn.execute(
        "INSERT INTO fire_cell_state (fire_event_id, cell_key, lat, lon) VALUES (?,?,?,?)",
        (event_id, "c0", lat, lon),
    )


def _obs(conn, wind_dir, observed_at, event_id=1):
    conn.execute(
        "INSERT INTO weather_obs (fire_event_id, lat, lon, observed_at, fetched_at, "
        "provider, wind_dir_deg) VALUES (?, 45.0, -1.0, ?, ?, 'synthetic', ?)",
        (event_id, observed_at, observed_at, wind_dir),
    )


def _setup(conn):
    invalidate_commune_index(conn)
    _fire(conn, 45.00, -1.00)
    # Commune EST : due est du feu, dans les 15 km (bord à ~7 km, centre ~9 km)
    _commune(conn, "EST", 44.96, 45.04, -0.92, -0.84)
    # Commune NORD : due nord, sert de témoin hors secteur quand le vent souffle vers l'est
    _commune(conn, "NORD", 45.08, 45.16, -1.04, -0.96)


def test_ouverture_des_premiere_mesure_dans_secteur(db):
    conn, config = db
    _setup(conn)
    _obs(conn, 270, "2026-07-22T12:00:00Z")  # vent d'ouest → aval est → EST dans le secteur
    r = recompute_direction_vent(conn, config, 1, stamp="2026-07-22T12:00:00Z")
    assert r["opened"] == 1
    rel = conn.execute(
        "SELECT code_insee FROM fe_commune_rel WHERE rel_type=? AND valid_to IS NULL", (REL,)
    ).fetchall()
    assert {x["code_insee"] for x in rel} == {"EST"}


def test_hysteresis_pas_de_fermeture_avant_trois_hors_secteur(db):
    conn, config = db
    _setup(conn)
    # 1) vent d'ouest → EST ouvre
    _obs(conn, 270, "2026-07-22T12:00:00Z")
    recompute_direction_vent(conn, config, 1, stamp="2026-07-22T12:00:00Z")
    # 2) vent du sud (souffle vers le nord) → EST hors secteur (1re mesure hors)
    _obs(conn, 180, "2026-07-22T12:15:00Z")
    r2 = recompute_direction_vent(conn, config, 1, stamp="2026-07-22T12:15:00Z")
    assert r2["closed"] == 0  # hystérésis : pas encore
    # 3) encore hors secteur (2e)
    _obs(conn, 180, "2026-07-22T12:30:00Z")
    r3 = recompute_direction_vent(conn, config, 1, stamp="2026-07-22T12:30:00Z")
    assert r3["closed"] == 0
    # la relation EST est toujours ouverte
    assert conn.execute(
        "SELECT valid_to FROM fe_commune_rel WHERE code_insee='EST' AND rel_type=?", (REL,)
    ).fetchone()["valid_to"] is None
    # 4) troisième mesure hors secteur → fermeture
    _obs(conn, 180, "2026-07-22T12:45:00Z")
    r4 = recompute_direction_vent(conn, config, 1, stamp="2026-07-22T12:45:00Z")
    assert r4["closed"] == 1
    est = conn.execute(
        "SELECT valid_to FROM fe_commune_rel WHERE code_insee='EST' AND rel_type=?", (REL,)
    ).fetchone()
    assert est["valid_to"] == "2026-07-22T12:45:00Z"


def test_retour_dans_secteur_annule_le_compte(db):
    """Un retour dans le secteur avant la 3e mesure hors remet le compteur à zéro."""
    conn, config = db
    _setup(conn)
    _obs(conn, 270, "2026-07-22T12:00:00Z")
    recompute_direction_vent(conn, config, 1, stamp="2026-07-22T12:00:00Z")
    _obs(conn, 180, "2026-07-22T12:15:00Z")  # hors (1)
    recompute_direction_vent(conn, config, 1, stamp="2026-07-22T12:15:00Z")
    _obs(conn, 270, "2026-07-22T12:30:00Z")  # de nouveau dans le secteur
    recompute_direction_vent(conn, config, 1, stamp="2026-07-22T12:30:00Z")
    _obs(conn, 180, "2026-07-22T12:45:00Z")  # hors (1 à nouveau)
    r = recompute_direction_vent(conn, config, 1, stamp="2026-07-22T12:45:00Z")
    assert r["closed"] == 0
    assert conn.execute(
        "SELECT valid_to FROM fe_commune_rel WHERE code_insee='EST' AND rel_type=?", (REL,)
    ).fetchone()["valid_to"] is None


def test_pas_de_relation_hors_portee(db):
    """Une commune bien orientée mais au-delà de d_vent_km n'entre pas en relation."""
    conn, config = db
    invalidate_commune_index(conn)
    _fire(conn, 45.00, -1.00)
    # Commune à l'est mais à ~30 km (au-delà des 15 km de portée)
    _commune(conn, "LOIN", 44.96, 45.04, -0.65, -0.55)
    _obs(conn, 270, "2026-07-22T12:00:00Z")
    r = recompute_direction_vent(conn, config, 1, stamp="2026-07-22T12:00:00Z")
    assert r["opened"] == 0
