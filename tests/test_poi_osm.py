"""Tests de l'importeur OSM du référentiel POI (Spec 06 §2.2, étape 2).

Vérifie la catégorisation par règles (config `[poi].osm_rules`), l'extraction du point
(node lat/lon ou way `center`), l'idempotence de l'upsert par clé naturelle, et la règle
multi-tags (EHPAD = social_facility + nursing_home). Utilise la **config livrée** pour
valider de bout en bout le fichier `config/params.toml`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vigifeu.model.db import connect, load_config, migrate
from vigifeu.referentiels.poi_osm import PoiImportError, import_poi_osm

FIXTURE = Path(__file__).parent / "fixtures" / "poi" / "overpass_gironde_ouest.json"
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
    r = import_poi_osm(conn, FIXTURE, config, imported_at=STAMP)
    assert r["upserted"] == 5
    assert r["skipped"] == 2  # hôtel (non catégorisé) + clinique way sans center
    assert r["by_category"] == {"camping": 1, "ecole": 2, "station_service": 1, "ehpad": 1}


def test_rows_persisted_way_center(conn, config):
    import_poi_osm(conn, FIXTURE, config, imported_at=STAMP)
    assert conn.execute("SELECT COUNT(*) AS n FROM poi").fetchone()["n"] == 5
    # Le way school passe par son `center` + source_ref 'way/id'.
    row = conn.execute(
        "SELECT lat, lon, category, nom, imported_at FROM poi WHERE source_ref='way/2001'"
    ).fetchone()
    assert row["category"] == "ecole"
    assert row["nom"] == "Collège des Dunes"
    assert row["lat"] == pytest.approx(44.7980)
    assert row["lon"] == pytest.approx(-1.1020)
    assert row["imported_at"] == STAMP


def test_idempotent(conn, config):
    import_poi_osm(conn, FIXTURE, config, imported_at=STAMP)
    import_poi_osm(conn, FIXTURE, config, imported_at="2026-09-01T00:00:00Z")
    assert conn.execute("SELECT COUNT(*) AS n FROM poi").fetchone()["n"] == 5  # upsert, pas de doublon
    row = conn.execute("SELECT imported_at FROM poi WHERE source_ref='node/1001'").fetchone()
    assert row["imported_at"] == "2026-09-01T00:00:00Z"  # rafraîchi au ré-import


def test_ehpad_exige_les_deux_tags(conn, config, tmp_path):
    """amenity=social_facility SANS social_facility=nursing_home → pas EHPAD (règle multi-tags)."""
    p = tmp_path / "one.json"
    p.write_text(
        json.dumps({"elements": [
            {"type": "node", "id": 9, "lat": 44.8, "lon": -1.1,
             "tags": {"amenity": "social_facility", "name": "Foyer"}},
        ]}),
        encoding="utf-8",
    )
    r = import_poi_osm(conn, p, config, imported_at=STAMP)
    assert r["upserted"] == 0 and r["skipped"] == 1


def test_config_absente_leve(conn):
    with pytest.raises(PoiImportError):
        import_poi_osm(conn, FIXTURE, {"poi": {}}, imported_at=STAMP)


def test_config_livree_a_des_regles():
    """Garde-fou : la config expédiée porte bien les règles OSM (jeu v1)."""
    rules = load_config().get("poi", {}).get("osm_rules")
    assert rules and any(rule["category"] == "camping" for rule in rules)
