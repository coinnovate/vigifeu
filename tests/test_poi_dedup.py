"""Tests de la déduplication inter-sources du référentiel POI (Spec 06 §2.3, étape 8d).

Un même enjeu physique présent dans plusieurs sources (OSM, BD TOPO, Géorisques) ne doit
compter qu'une fois : on garde le POI de la source la plus prioritaire (config
`source_priority`), les autres sont marqués `superseded_by` (jamais supprimés, P1). La dédup
est **inter-source seulement** et **par catégorie**. Vérifie aussi que l'index feu↔POI ne voit
que les canoniques.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vigifeu.engine.relations import build_poi_index, recompute_poi_dedup
from vigifeu.model.db import connect, load_config, migrate
from vigifeu.referentiels.poi_bdtopo import import_poi_bdtopo
from vigifeu.referentiels.poi_osm import import_poi_osm

FIX_DIR = Path(__file__).parent / "fixtures" / "poi"
STAMP = "2026-08-01T00:00:00Z"


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture()
def config():
    return load_config()


def _add(conn, source, ref, category, lat, lon):
    conn.execute(
        "INSERT INTO poi (source, source_ref, category, lat, lon, imported_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (source, ref, category, lat, lon, STAMP),
    )


def _superseded(conn):
    return {
        r["source_ref"]: r["superseded_by"]
        for r in conn.execute("SELECT source_ref, superseded_by FROM poi")
    }


def test_meme_camping_deux_sources_dedupe(conn, config):
    _add(conn, "osm", "node/1", "camping", 44.8710, -1.1350)
    _add(conn, "bdtopo", "SURF_1", "camping", 44.8711, -1.1351)  # ~15 m
    conn.commit()
    r = recompute_poi_dedup(conn, config)
    assert r == {"superseded": 1, "canonical": 1}
    sup = _superseded(conn)
    canon_id = conn.execute("SELECT id FROM poi WHERE source='bdtopo'").fetchone()["id"]
    assert sup["SURF_1"] is None          # BD TOPO prioritaire → canonique
    assert sup["node/1"] == canon_id       # OSM masqué, pointe vers le canonique


def test_meme_source_pas_de_dedup(conn, config):
    # Deux campings OSM proches = deux enjeux distincts (dédup inter-source seulement).
    _add(conn, "osm", "node/1", "camping", 44.8710, -1.1350)
    _add(conn, "osm", "node/2", "camping", 44.8711, -1.1351)
    conn.commit()
    r = recompute_poi_dedup(conn, config)
    assert r["superseded"] == 0


def test_categories_differentes_pas_de_dedup(conn, config):
    _add(conn, "osm", "node/1", "camping", 44.8710, -1.1350)
    _add(conn, "bdtopo", "SURF_1", "ecole", 44.8710, -1.1350)  # même point, autre catégorie
    conn.commit()
    r = recompute_poi_dedup(conn, config)
    assert r["superseded"] == 0


def test_priorite_source_respectee(conn, config):
    # georisques prime sur osm (source_priority = [bdtopo, georisques, osm]).
    _add(conn, "osm", "node/1", "icpe_seveso", 44.8710, -1.1350)
    _add(conn, "georisques", "AIOT_1", "icpe_seveso", 44.8711, -1.1351)
    conn.commit()
    recompute_poi_dedup(conn, config)
    sup = _superseded(conn)
    assert sup["AIOT_1"] is None
    assert sup["node/1"] is not None


def test_trois_sources_un_seul_canonique(conn, config):
    # osm + bdtopo + osm sur le même camping → BD TOPO canonique, les deux OSM masqués.
    _add(conn, "osm", "node/1", "camping", 44.8710, -1.1350)
    _add(conn, "osm", "node/2", "camping", 44.8711, -1.1351)
    _add(conn, "bdtopo", "SURF_1", "camping", 44.87105, -1.13505)
    conn.commit()
    r = recompute_poi_dedup(conn, config)
    # node/2 est à ~15 m de node/1 (même source, pas dédupliqué entre eux) mais tous deux
    # dans le rayon de SURF_1 (autre source) → absorbés par le canonique BD TOPO.
    assert r == {"superseded": 2, "canonical": 1}
    canon = conn.execute("SELECT id FROM poi WHERE source='bdtopo'").fetchone()["id"]
    assert all(v == canon for k, v in _superseded(conn).items() if k != "SURF_1")


def test_idempotent(conn, config):
    _add(conn, "osm", "node/1", "camping", 44.8710, -1.1350)
    _add(conn, "bdtopo", "SURF_1", "camping", 44.8711, -1.1351)
    conn.commit()
    r1 = recompute_poi_dedup(conn, config)
    r2 = recompute_poi_dedup(conn, config)
    assert r1 == r2 == {"superseded": 1, "canonical": 1}


def test_index_feu_poi_exclut_les_doublons(conn, config):
    """build_poi_index ne voit que les canoniques (pas de relation feu↔POI en double)."""
    import_poi_osm(conn, FIX_DIR / "overpass_gironde_ouest.json", config, imported_at=STAMP)
    import_poi_bdtopo(conn, FIX_DIR / "bdtopo_gironde_ouest.geojson", config, imported_at=STAMP)
    total = conn.execute("SELECT COUNT(*) AS n FROM poi").fetchone()["n"]
    assert total == 8  # 5 OSM + 3 BD TOPO
    recompute_poi_dedup(conn, config)
    # Le camping BD TOPO recouvre le camping OSM (node/1001) → 1 doublon masqué.
    assert len(build_poi_index(conn)) == 7
