"""Import du référentiel commune (Lot 3, L3.1).

Fixture : extrait Gironde-ouest (165 communes autour du foyer Saumos), fabriqué
depuis geo.api.gouv.fr — format GeoJSON WGS84, comme le fera l'utilisateur pour un
export léger. La production lira un GeoPackage Admin Express (même normalisation).
"""

from __future__ import annotations

from pathlib import Path

from shapely import wkb as shapely_wkb
from shapely.geometry import Polygon
from shapely.wkt import loads as wkt_loads

from vigifeu.referentiels.communes import (
    _choose_layer,
    _decode_gpb,
    _normalize,
    import_communes,
    slugify,
)

FIXTURE = Path(__file__).parent / "fixtures" / "communes" / "gironde-ouest.geojson"


def test_slugify():
    assert slugify("Saumos") == "saumos"
    assert slugify("Lège-Cap-Ferret") == "lege-cap-ferret"
    assert slugify("Saint-Jean-d'Illac") == "saint-jean-d-illac"
    assert slugify("Sainte-Hélène") == "sainte-helene"


def test_import_communes_fixture(db):
    conn, _ = db
    res = import_communes(conn, FIXTURE, millesime="test-gironde")
    assert res["imported"] == 165
    n = conn.execute("SELECT COUNT(*) AS n FROM commune").fetchone()["n"]
    assert n == 165


def test_saumos_attributs(db):
    conn, _ = db
    import_communes(conn, FIXTURE, millesime="test-gironde")
    c = conn.execute(
        "SELECT * FROM commune WHERE code_insee='33503'"
    ).fetchone()
    assert c["nom"] == "Saumos"
    assert c["slug"] == "saumos"
    assert c["dept"] == "33"
    assert c["referentiel_millesime"] == "test-gironde"
    # centroïde plausible (Gironde ouest) et géométrie WKT valide en WGS84
    assert 44.8 < c["centroid_lat"] < 45.1
    assert -1.2 < c["centroid_lon"] < -0.9
    assert c["surface_ha"] > 1000  # Saumos ~50 km²
    poly = wkt_loads(c["geometry_wkt"])
    assert poly.is_valid and not poly.is_empty


def test_import_idempotent(db):
    """Rejouer un millésime met à jour, ne duplique pas (upsert par code_insee)."""
    conn, _ = db
    import_communes(conn, FIXTURE, millesime="v1")
    import_communes(conn, FIXTURE, millesime="v2")
    assert conn.execute("SELECT COUNT(*) AS n FROM commune").fetchone()["n"] == 165
    # le second import a bien actualisé le millésime
    m = conn.execute(
        "SELECT referentiel_millesime FROM commune WHERE code_insee='33503'"
    ).fetchone()["referentiel_millesime"]
    assert m == "v2"


def test_normalize_admin_express_4_0():
    """Schéma Admin Express 4-0 (thème administratif) : noms de colonnes français."""
    raw = {
        "code_insee": "33503",
        "nom_officiel": "Saumos",
        "code_insee_du_departement": "33",
        "code_insee_de_la_region": "75",
        "codes_siren_des_epci": "243301389",
        "population": 550,
        "statut": "Commune simple",  # champ ignoré
    }
    got = _normalize(raw)
    assert got == {
        "code_insee": "33503",
        "nom": "Saumos",
        "dept": "33",
        "region": "75",
        "epci_code": "243301389",
        "population": 550,
    }


def _cols(*pairs):
    return [{"table_name": t, "column_name": c} for t, c in pairs]


def test_choose_layer_prend_commune_polygone():
    """Auto-sélection : la couche `commune` (polygone), jamais chef_lieu_de_commune."""
    # ordre réel du GeoPackage Admin Express 4-0 : chef_lieu_de_commune AVANT commune
    cols = _cols(
        ("chef_lieu_de_commune", "geometrie"),
        ("commune_associee_ou_deleguee", "geometrie"),
        ("commune", "geometrie"),
        ("departement", "geometrie"),
    )
    assert _choose_layer(cols, None) == ("commune", "geometrie")


def test_choose_layer_explicite():
    cols = _cols(("commune", "geom"), ("departement", "geom"))
    assert _choose_layer(cols, "departement") == ("departement", "geom")


def test_decode_gpb_roundtrip():
    """Le décodage GeoPackage Binary (en-tête GPB + WKB) reconstruit la géométrie.

    On fabrique un blob GPB minimal (flags=0 : pas d'enveloppe, en-tête little-endian)
    autour du WKB d'un carré, sans dépendre d'un fichier .gpkg réel.
    """
    poly = Polygon([(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)])
    wkb = shapely_wkb.dumps(poly)
    # 'GP', version 0, flags 0 (pas d'enveloppe), srs_id 2154 (little-endian)
    header = b"GP" + bytes([0, 0]) + (2154).to_bytes(4, "little")
    got = _decode_gpb(header + wkb)
    assert got.equals(poly)
