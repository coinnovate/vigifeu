"""Tests des lectures socle du canal contributif (Spec 10 §4, étape 3).

Feux proches (< rayon → feu ; au-delà → aucun ; feux non publiés/fusionnés exclus),
commune du hotspot (point dans polygone → code_insee ; hors commune → NULL), et
l'endpoint `GET /api/contrib/feux-proches` (validation, bornage territoire, dégradation).
"""

from __future__ import annotations

import copy

import pytest

from vigifeu.contrib.app import create_app
from vigifeu.contrib.db import connect_socle_readonly
from vigifeu.contrib.socle import commune_du_point, feux_proches
from vigifeu.model.db import connect, load_config, migrate

# Point d'ancrage des tests (Gironde, zone couverte firms_bbox).
P_LAT, P_LON = 44.7500, -1.1000


def _carre_wkt(lat: float, lon: float, demi: float = 0.05) -> str:
    """POLYGON carré (WGS84 lon/lat) centré sur (lat, lon), demi-côté `demi` degrés."""
    s, n = lat - demi, lat + demi
    w, e = lon - demi, lon + demi
    return f"POLYGON(({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))"


def _seed_socle(path: str) -> None:
    """Socle minimale : deux feux publiés (proche/lointain), un non publié, un fusionné, une commune."""
    c = connect(path)
    migrate(c)
    c.execute(
        "INSERT INTO satellite_source (id, code, platform, instrument) "
        "VALUES (1, 'VIIRS_SNPP_NRT', 'SNPP', 'VIIRS')"
    )
    c.execute(
        "INSERT INTO ingestion_run (id, source, started_at, status) "
        "VALUES (1, 'test', '2026-08-07T00:00:00Z', 'ok')"
    )

    # fire_event : (id, public_id, lifecycle)
    feux = [
        (1, "2026-proche", "actif"),      # hotspot à ~4 km
        (2, "2026-lointain", "actif"),    # hotspot à ~22 km
        (3, None, "actif"),               # NON publié → jamais proposé
        (4, "2026-fusionne", "fusionne"), # fusionné → redirigé, exclu
    ]
    for fid, pub, life in feux:
        c.execute(
            "INSERT INTO fire_event (id, public_id, created_at, lifecycle) VALUES (?,?,?,?)",
            (fid, pub, "2026-08-07T00:00:00Z", life),
        )
        c.execute(
            "INSERT INTO fire_event_version (id, fire_event_id, version_n, computed_at) "
            "VALUES (?,?,1,?)",
            (fid, fid, "2026-08-07T00:00:00Z"),
        )

    # hotspot_raw : (id, lat, lon) rattaché au feu de même id
    hotspots = [
        (1, P_LAT + 0.04, P_LON),   # ~4.4 km  → feu 1
        (2, P_LAT + 0.20, P_LON),   # ~22 km   → feu 2
        (3, P_LAT + 0.02, P_LON + 0.001),   # ~2 km    → feu 3 (non publié)
        (4, P_LAT + 0.02, P_LON - 0.001),   # ~2 km    → feu 4 (fusionné)
    ]
    for hid, lat, lon in hotspots:
        c.execute(
            "INSERT INTO hotspot_raw (id, source_id, lat, lon, acq_at, ingested_at, ingestion_run_id) "
            "VALUES (?, 1, ?, ?, '2026-08-07T00:00:00Z', '2026-08-07T00:05:00Z', 1)",
            (hid, lat, lon),
        )
        c.execute(
            "INSERT INTO fe_hotspot (fire_event_version_id, hotspot_id) VALUES (?, ?)",
            (hid, hid),
        )

    # Commune contenant le point d'ancrage (point-dans-polygone).
    c.execute(
        "INSERT INTO commune (code_insee, slug, nom, geometry_wkt, centroid_lat, centroid_lon) "
        "VALUES ('33999', 'commune-test', 'Commune Test', ?, ?, ?)",
        (_carre_wkt(P_LAT, P_LON), P_LAT, P_LON),
    )
    c.commit()
    c.close()


@pytest.fixture()
def socle_path(tmp_path):
    p = str(tmp_path / "socle.db")
    _seed_socle(p)
    return p


@pytest.fixture()
def ro(socle_path):
    conn = connect_socle_readonly(socle_path)
    yield conn
    conn.close()


# --- feux_proches ---------------------------------------------------------

def test_feu_a_moins_de_rayon_est_retourne(ro):
    feux = feux_proches(ro, P_LAT, P_LON, rayon_max_km=10.0)
    assert [f["public_id"] for f in feux] == ["2026-proche"]
    f = feux[0]
    assert f["fire_event_id"] == 1
    assert f["hotspot_raw_id"] == 1
    assert 3.0 < f["distance_km"] < 6.0        # ~4.4 km


def test_feu_lointain_hors_rayon_absent(ro):
    """Le feu à ~22 km n'apparaît pas dans un rayon de 10 km (refus, §4)."""
    feux = feux_proches(ro, P_LAT, P_LON, rayon_max_km=10.0)
    assert "2026-lointain" not in [f["public_id"] for f in feux]
    # Rayon élargi : il devient visible → confirme que seule la distance l'excluait.
    large = feux_proches(ro, P_LAT, P_LON, rayon_max_km=30.0)
    assert "2026-lointain" in [f["public_id"] for f in large]


def test_feux_non_publie_et_fusionne_exclus(ro):
    """Feu sans public_id et feu fusionné jamais proposés, même très proches."""
    feux = feux_proches(ro, P_LAT, P_LON, rayon_max_km=10.0)
    ids = {f["fire_event_id"] for f in feux}
    assert 3 not in ids and 4 not in ids


def test_tri_par_distance_croissante(ro):
    feux = feux_proches(ro, P_LAT, P_LON, rayon_max_km=30.0)
    distances = [f["distance_km"] for f in feux]
    assert distances == sorted(distances)


# --- commune_du_point -----------------------------------------------------

def test_point_dans_commune_retourne_code_insee(ro):
    assert commune_du_point(ro, P_LAT, P_LON) == "33999"


def test_point_hors_commune_retourne_none(ro):
    """Point hors du carré (offshore) → aucune commune contenante → NULL (§7.4)."""
    assert commune_du_point(ro, P_LAT, P_LON - 0.30) is None


# --- endpoint /feux-proches ----------------------------------------------

@pytest.fixture()
def client(socle_path, tmp_path):
    config = copy.deepcopy(load_config("config/params.toml"))
    config["contributions"]["db_path"] = str(tmp_path / "contributions.db")
    config["general"]["db_path"] = socle_path
    app = create_app(config)
    app.testing = True
    return app.test_client()


def test_endpoint_feux_proches_ok(client):
    r = client.get(f"/api/contrib/feux-proches?lat={P_LAT}&lon={P_LON}")
    assert r.status_code == 200
    feux = r.get_json()["feux"]
    assert [f["public_id"] for f in feux] == ["2026-proche"]


def test_endpoint_lat_lon_manquants_400(client):
    assert client.get("/api/contrib/feux-proches?lat=44.7").status_code == 400
    assert client.get("/api/contrib/feux-proches").status_code == 400
    assert client.get("/api/contrib/feux-proches?lat=x&lon=y").status_code == 400


def test_endpoint_hors_territoire_400(client):
    """(0,0) hors firms_bbox → 400 (bornage territoire, §4)."""
    assert client.get("/api/contrib/feux-proches?lat=0&lon=0").status_code == 400


def test_endpoint_socle_absente_liste_vide(tmp_path):
    """Socle non déployée → 200 + liste vide (dégradation gracieuse, jamais 500)."""
    config = copy.deepcopy(load_config("config/params.toml"))
    config["contributions"]["db_path"] = str(tmp_path / "contributions.db")
    config["general"]["db_path"] = str(tmp_path / "absente.db")
    app = create_app(config)
    app.testing = True
    r = app.test_client().get(f"/api/contrib/feux-proches?lat={P_LAT}&lon={P_LON}")
    assert r.status_code == 200
    assert r.get_json()["feux"] == []
