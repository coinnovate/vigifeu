"""Tests de l'importeur Géorisques du référentiel POI — Seveso seul (Spec 06 §2.2/§8).

Source réelle = **API JSON Géorisques** (camelCase), pas un CSV (hypothèse de format corrigée
sur la vraie donnée). Vérifie le filtre Seveso (haut/bas gardés, Non Seveso / null ignorés),
les deux voies de coordonnées (longitude/latitude WGS84 ; coordonnee[XY]AIOT Lambert-93
reprojetés), l'idempotence, et la catégorie unique `icpe_seveso`. Config livrée (statuts Seveso).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vigifeu.model.db import connect, load_config, migrate
from vigifeu.referentiels.poi_georisques import (
    PoiGeorisquesImportError,
    import_poi_georisques,
)

FIXTURE = Path(__file__).parent / "fixtures" / "poi" / "georisques_seveso.json"
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
    assert r["upserted"] == 2  # seuil haut (WGS84) + seuil bas (L93)
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


def test_liste_nue_acceptee(conn, config, tmp_path):
    """Le JSON peut être une liste nue (pages concaténées) au lieu de {data:[...]}."""
    p = tmp_path / "liste.json"
    p.write_text(json.dumps([
        {"codeAIOT": "X1", "raisonSociale": "S", "statutSeveso": "Seveso seuil haut",
         "longitude": -1.0, "latitude": 44.9},
    ]), encoding="utf-8")
    r = import_poi_georisques(conn, p, config, imported_at=STAMP)
    assert r["upserted"] == 1


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
