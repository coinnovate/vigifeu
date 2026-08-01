"""Tests de l'importeur Géorisques du référentiel POI — Seveso seul (Spec 06 §2.2/§8).

Vérifie le filtre Seveso (haut/bas gardés, Non Seveso ignoré), les deux voies de coordonnées
(longitude/latitude WGS84 ; x/y Lambert-93 reprojetés), l'idempotence, et la catégorie unique
`icpe_seveso` (celle du lexique). Utilise la **config livrée** (statuts Seveso).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vigifeu.model.db import connect, load_config, migrate
from vigifeu.referentiels.poi_georisques import (
    PoiGeorisquesImportError,
    import_poi_georisques,
)

FIXTURE = Path(__file__).parent / "fixtures" / "poi" / "georisques_gironde_ouest.csv"
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


def test_filtre_seveso(conn, config):
    r = import_poi_georisques(conn, FIXTURE, config, imported_at=STAMP)
    assert r["upserted"] == 2  # seuil haut + seuil bas
    assert r["skipped"] == 2   # Non Seveso + Seveso sans coordonnées
    cats = {row["category"] for row in conn.execute("SELECT DISTINCT category FROM poi")}
    assert cats == {"icpe_seveso"}


def test_coords_wgs84_directes(conn, config):
    import_poi_georisques(conn, FIXTURE, config, imported_at=STAMP)
    row = conn.execute(
        "SELECT lat, lon, source, nom FROM poi WHERE source_ref='0064.00001'"
    ).fetchone()
    assert row["source"] == "georisques"
    assert row["nom"] == "Dépôt pétrolier de la Pointe"
    assert row["lat"] == pytest.approx(44.8000)
    assert row["lon"] == pytest.approx(-1.1500)


def test_coords_lambert93_reprojetees(conn, config):
    import_poi_georisques(conn, FIXTURE, config, imported_at=STAMP)
    row = conn.execute("SELECT lat, lon FROM poi WHERE source_ref='0064.00002'").fetchone()
    assert row["lat"] == pytest.approx(44.7800, abs=1e-4)
    assert row["lon"] == pytest.approx(-1.1800, abs=1e-4)


def test_idempotent(conn, config):
    import_poi_georisques(conn, FIXTURE, config, imported_at=STAMP)
    import_poi_georisques(conn, FIXTURE, config, imported_at="2026-09-01T00:00:00Z")
    assert conn.execute("SELECT COUNT(*) AS n FROM poi").fetchone()["n"] == 2
    row = conn.execute("SELECT imported_at FROM poi WHERE source_ref='0064.00001'").fetchone()
    assert row["imported_at"] == "2026-09-01T00:00:00Z"


def test_config_absente_leve(conn):
    with pytest.raises(PoiGeorisquesImportError):
        import_poi_georisques(conn, FIXTURE, {"poi": {}}, imported_at=STAMP)


def test_config_livree_a_les_statuts():
    statuts = load_config().get("poi", {}).get("georisques_seveso_statuts")
    assert statuts and any("seveso" in s.lower() for s in statuts)
