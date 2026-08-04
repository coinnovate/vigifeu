"""Commune principale d'un feu → mot-clé presse (Spec 09 §3, étape 3).

Trois cas : emprise (commune d'origine), à défaut proximité la plus proche, à défaut None
(pas de mot-clé fiable → pas d'appel). Réutilise `publish.origin_commune`.
"""

from __future__ import annotations

import pytest

from vigifeu.ingest import bulletins

NOW = "2026-07-25T12:00:00Z"


def _commune(conn, code_insee, slug, nom):
    conn.execute(
        "INSERT INTO commune (code_insee, slug, nom) VALUES (?, ?, ?)",
        (code_insee, slug, nom),
    )


def _feu(conn) -> int:
    conn.execute(
        "INSERT INTO fire_event (created_at, qualification, first_acq_at) "
        "VALUES (?, 'vegetation_confirme', ?)",
        (NOW, NOW),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _rel(conn, fire_id, code_insee, rel_type, distance_km=None):
    conn.execute(
        "INSERT INTO fe_commune_rel (fire_event_id, code_insee, rel_type, distance_km, valid_from) "
        "VALUES (?, ?, ?, ?, ?)",
        (fire_id, code_insee, rel_type, distance_km, NOW),
    )


def test_commune_emprise(db):
    conn, config = db
    _commune(conn, "33001", "saumos", "Saumos")
    fid = _feu(conn)
    _rel(conn, fid, "33001", "emprise_dans_commune")
    conn.commit()
    assert bulletins.commune_principale(conn, fid)["nom"] == "Saumos"
    assert bulletins.mots_cles_pour_feu(conn, config, fid) == "incendie Saumos"


def test_repli_proximite_la_plus_proche(db):
    """Sans emprise : la commune de proximité courante la plus proche (distance min)."""
    conn, config = db
    _commune(conn, "33333", "le-porge", "Le Porge")
    _commune(conn, "33236", "lacanau", "Lacanau")
    fid = _feu(conn)
    _rel(conn, fid, "33236", "a_moins_de_10km", distance_km=7.8)
    _rel(conn, fid, "33333", "a_moins_de_5km", distance_km=2.3)
    conn.commit()
    assert bulletins.commune_principale(conn, fid)["nom"] == "Le Porge"
    assert bulletins.mots_cles_pour_feu(conn, config, fid) == "incendie Le Porge"


def test_proximite_fermee_ignoree(db):
    """Une relation de proximité fermée (valid_to) ne sert pas de repli."""
    conn, config = db
    _commune(conn, "33333", "le-porge", "Le Porge")
    fid = _feu(conn)
    conn.execute(
        "INSERT INTO fe_commune_rel (fire_event_id, code_insee, rel_type, distance_km, valid_from, valid_to) "
        "VALUES (?, '33333', 'a_moins_de_5km', 2.3, ?, ?)",
        (fid, NOW, "2026-07-26T00:00:00Z"),
    )
    conn.commit()
    assert bulletins.commune_principale(conn, fid) is None
    assert bulletins.mots_cles_pour_feu(conn, config, fid) is None


def test_aucune_commune(db):
    """Feu sans relation commune : pas de mot-clé fiable → None (pas d'appel)."""
    conn, config = db
    fid = _feu(conn)
    conn.commit()
    assert bulletins.commune_principale(conn, fid) is None
    assert bulletins.mots_cles_pour_feu(conn, config, fid) is None
