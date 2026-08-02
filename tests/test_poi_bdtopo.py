"""Tests de l'importeur BD TOPO du référentiel POI (Spec 06 §2.2/§8, étape 8).

Vérifie la catégorisation par `nature` (config `[poi].bdtopo_rules`, insensible casse/accents),
l'extraction du point représentatif (centroïde d'une surface, point tel quel), l'idempotence de
l'upsert par (`source`, `source_ref`=cleabs) et la coexistence avec les POI OSM (source distincte).
Utilise la **config livrée** pour valider `config/params.toml` de bout en bout.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from shapely import wkb as shapely_wkb
from shapely.geometry import Point

from vigifeu.engine import geo
from vigifeu.model.db import connect, load_config, migrate
from vigifeu.referentiels.poi_bdtopo import PoiBdtopoImportError, import_poi_bdtopo
from vigifeu.referentiels.poi_osm import import_poi_osm


def _gpb_l93(lat: float, lon: float) -> bytes:
    """Blob GeoPackage Binary (en-tête GPB flags=0 + WKB) d'un point en Lambert-93."""
    x, y = geo.project(lat, lon)
    wkb = shapely_wkb.dumps(Point(x, y))
    header = b"GP" + bytes([0, 0]) + (2154).to_bytes(4, "little")
    return header + wkb


def _make_bdtopo_gpkg(path: Path) -> None:
    """Fabrique un GeoPackage BD TOPO minimal à DEUX couches géométriques (santé +
    enseignement), sans dépendre d'un .gpkg réel — pour couvrir le balayage multi-couches
    et la reprojection L93→WGS84 du chemin production."""
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT)"
    )
    for table in ("etablissement_de_sante", "enseignement"):
        db.execute(f'CREATE TABLE "{table}" (cleabs TEXT, nature TEXT, toponyme TEXT, geom BLOB)')
        db.execute(
            "INSERT INTO gpkg_geometry_columns (table_name, column_name) VALUES (?, 'geom')",
            (table,),
        )
    db.execute(
        'INSERT INTO etablissement_de_sante VALUES (?, ?, ?, ?)',
        ("SANTE_1", "Hôpital", "Hôpital du Bassin", _gpb_l93(44.7500, -1.2050)),
    )
    db.execute(
        'INSERT INTO enseignement VALUES (?, ?, ?, ?)',
        ("ENS_1", "Lycée", "Lycée de la Forêt", _gpb_l93(44.8300, -1.1600)),
    )
    db.commit()
    db.close()

FIX_DIR = Path(__file__).parent / "fixtures" / "poi"
FIXTURE = FIX_DIR / "bdtopo_gironde_ouest.geojson"
OSM_FIXTURE = FIX_DIR / "overpass_gironde_ouest.json"
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


def test_import_counts(conn, config):
    r = import_poi_bdtopo(conn, FIXTURE, config, imported_at=STAMP)
    assert r["upserted"] == 3
    assert r["skipped"] == 1  # « Mairie » : nature non catégorisée
    assert r["by_category"] == {"camping": 1, "ecole": 1, "hopital": 1}


def test_surface_donne_son_centroide(conn, config):
    import_poi_bdtopo(conn, FIXTURE, config, imported_at=STAMP)
    row = conn.execute(
        "SELECT lat, lon, source, category, nom FROM poi WHERE source_ref='SURFACE_ACTIVITE_0001'"
    ).fetchone()
    assert row["source"] == "bdtopo"
    assert row["category"] == "camping"
    assert row["nom"] == "Camping de la Grigne"
    # Centroïde du carré [-1.1356..-1.1346] x [44.8706..44.8716].
    assert row["lat"] == pytest.approx(44.8711, abs=1e-4)
    assert row["lon"] == pytest.approx(-1.1351, abs=1e-4)


def test_idempotent(conn, config):
    import_poi_bdtopo(conn, FIXTURE, config, imported_at=STAMP)
    import_poi_bdtopo(conn, FIXTURE, config, imported_at="2026-09-01T00:00:00Z")
    assert conn.execute("SELECT COUNT(*) AS n FROM poi").fetchone()["n"] == 3
    row = conn.execute(
        "SELECT imported_at FROM poi WHERE source_ref='ENSEIGNEMENT_0002'"
    ).fetchone()
    assert row["imported_at"] == "2026-09-01T00:00:00Z"


def test_coexiste_avec_osm(conn, config):
    """OSM et BD TOPO cohabitent (clés naturelles disjointes) — la dédup est une passe à part."""
    import_poi_osm(conn, OSM_FIXTURE, config, imported_at=STAMP)
    import_poi_bdtopo(conn, FIXTURE, config, imported_at=STAMP)
    n_osm = conn.execute("SELECT COUNT(*) AS n FROM poi WHERE source='osm'").fetchone()["n"]
    n_bd = conn.execute("SELECT COUNT(*) AS n FROM poi WHERE source='bdtopo'").fetchone()["n"]
    assert n_osm == 5 and n_bd == 3


def test_geopackage_multi_couches_reprojete(conn, config, tmp_path):
    """Chemin production : balayage de plusieurs couches + reprojection Lambert-93 → WGS84."""
    gpkg = tmp_path / "bdtopo.gpkg"
    _make_bdtopo_gpkg(gpkg)
    r = import_poi_bdtopo(conn, gpkg, config, imported_at=STAMP)
    assert r["upserted"] == 2
    assert r["by_category"] == {"hopital": 1, "ecole": 1}
    row = conn.execute("SELECT lat, lon FROM poi WHERE source_ref='ENS_1'").fetchone()
    assert row["lat"] == pytest.approx(44.8300, abs=1e-4)
    assert row["lon"] == pytest.approx(-1.1600, abs=1e-4)


def test_config_absente_leve(conn):
    with pytest.raises(PoiBdtopoImportError):
        import_poi_bdtopo(conn, FIXTURE, {"poi": {}}, imported_at=STAMP)


def test_config_livree_a_des_regles_bdtopo():
    rules = load_config().get("poi", {}).get("bdtopo_rules")
    assert rules and any(rule["category"] == "camping" for rule in rules)
