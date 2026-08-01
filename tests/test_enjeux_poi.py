"""Enjeux POI sur la fiche feu — lexique agrégé + loader (Spec 06 §4, étape 4).

Vérifie que l'énoncé public reste AGRÉGÉ (comptes + catégorie, jamais de nom ni de
capacité), la pluralisation et l'ordre d'affichage, et que le loader ne surface que les
paliers emprise / < 5 km courants (10/20 km et relations fermées ignorés).
"""

from __future__ import annotations

import pytest

from vigifeu.generate.feu import _enjeux_poi
from vigifeu.lexique import fr
from vigifeu.model.db import connect, migrate


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    c.execute("INSERT INTO fire_event (id, created_at, qualification, lifecycle) "
              "VALUES (1, '2026-07-22T00:00:00Z', 'vegetation_confirme', 'actif')")
    yield c
    c.close()


def _poi(conn, category, ref):
    conn.execute(
        "INSERT INTO poi (source, source_ref, category, lat, lon, imported_at) "
        "VALUES ('osm', ?, ?, 45.0, -1.0, '2026-08-01T00:00:00Z')",
        (ref, category),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _rel(conn, poi_id, rel_type, *, closed=False):
    conn.execute(
        "INSERT INTO fe_poi_rel (fire_event_id, poi_id, rel_type, valid_from, valid_to) "
        "VALUES (1, ?, ?, '2026-07-22T12:00:00Z', ?)",
        (poi_id, rel_type, "2026-07-23T00:00:00Z" if closed else None),
    )


def test_phrase_emprise():
    assert (fr.phrase_enjeux_poi("emprise", {"camping": 2, "ecole": 1})
            == "Dans la zone détectée du feu : 2 campings et 1 établissement scolaire")


def test_phrase_proximite_ordre_pluriel_et_invariable():
    # ordre fixe (camping, ehpad, station_service) quel que soit l'ordre d'entrée ; EHPAD invariable.
    s = fr.phrase_enjeux_poi("proximite", {"station_service": 2, "camping": 1, "ehpad": 1})
    assert s == "À proximité (moins de 5 km) : 1 camping, 1 EHPAD et 2 stations-service"


def test_phrase_vide():
    assert fr.phrase_enjeux_poi("emprise", {}) == ""


def test_phrase_jamais_de_nom():
    """Structurellement, la phrase ne prend que des comptes → aucun nom ne peut fuiter (P0)."""
    s = fr.phrase_enjeux_poi("emprise", {"icpe_seveso": 1})
    assert s == "Dans la zone détectée du feu : 1 site Seveso"


def test_loader_agrege_et_ignore_lointains_et_fermes(conn):
    c1, c2 = _poi(conn, "camping", "n/1"), _poi(conn, "camping", "n/2")
    e1 = _poi(conn, "ecole", "n/3")
    h1 = _poi(conn, "hopital", "n/4")
    loin = _poi(conn, "camping", "n/5")
    ferme = _poi(conn, "ecole", "n/6")
    _rel(conn, c1, "emprise")
    _rel(conn, c2, "emprise")
    _rel(conn, e1, "emprise")
    _rel(conn, h1, "a_moins_de_5km")
    _rel(conn, loin, "a_moins_de_10km")           # palier lointain → non surfacé
    _rel(conn, ferme, "emprise", closed=True)     # relation fermée → non surfacée
    conn.commit()

    phrases = _enjeux_poi(conn, 1)
    assert phrases == [
        "Dans la zone détectée du feu : 2 campings et 1 établissement scolaire",
        "À proximité (moins de 5 km) : 1 hôpital",
    ]


def test_loader_vide_sans_relation(conn):
    assert _enjeux_poi(conn, 1) == []
