"""Tests du dépôt de contribution — POST /api/contrib/deposer (Spec 10 §4, étape 4).

Dépôt nominal (ligne `soumise` + `captured_at` + vignette + `code_insee`), garde-fous
(consentement, e-mail, image, ancre hors rayon), anti-abus IP (blocklist, quota),
idempotence `sha256`, écriture des images **au dépôt uniquement**, et plafond d'upload.
"""

from __future__ import annotations

import copy
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from vigifeu.contrib.app import create_app
from vigifeu.contrib.db import connect_contrib
from vigifeu.contrib.ip import hash_ip
from vigifeu.model.db import connect, load_config, migrate

SECRET = "test-secret"
P_LAT, P_LON = 44.7500, -1.1000
HS_LAT, HS_LON = P_LAT + 0.01, P_LON  # hotspot à ~1,1 km de la géoloc


def _carre_wkt(lat: float, lon: float, demi: float = 0.05) -> str:
    s, n = lat - demi, lat + demi
    w, e = lon - demi, lon + demi
    return f"POLYGON(({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))"


def _seed_socle(path: str) -> None:
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
    c.execute(
        "INSERT INTO fire_event (id, public_id, created_at, lifecycle) "
        "VALUES (1, '2026-proche', '2026-08-07T00:00:00Z', 'actif')"
    )
    c.execute(
        "INSERT INTO fire_event_version (id, fire_event_id, version_n, computed_at) "
        "VALUES (1, 1, 1, '2026-08-07T00:00:00Z')"
    )
    c.execute(
        "INSERT INTO hotspot_raw (id, source_id, lat, lon, acq_at, ingested_at, ingestion_run_id) "
        "VALUES (1, 1, ?, ?, '2026-08-07T00:00:00Z', '2026-08-07T00:05:00Z', 1)",
        (HS_LAT, HS_LON),
    )
    c.execute("INSERT INTO fe_hotspot (fire_event_version_id, hotspot_id) VALUES (1, 1)")
    c.execute(
        "INSERT INTO commune (code_insee, slug, nom, geometry_wkt, centroid_lat, centroid_lon) "
        "VALUES ('33999', 'commune-test', 'Commune Test', ?, ?, ?)",
        (_carre_wkt(P_LAT, P_LON), P_LAT, P_LON),
    )
    c.commit()
    c.close()


def _jpeg(largeur=1200, hauteur=900, couleur=(200, 80, 40)) -> bytes:
    tampon = BytesIO()
    Image.new("RGB", (largeur, hauteur), couleur).save(tampon, format="JPEG")
    return tampon.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Socle semée + config redirigée vers tmp + secret d'env armé."""
    monkeypatch.setenv("CONTRIB_HASH_SECRET", SECRET)
    socle = str(tmp_path / "socle.db")
    _seed_socle(socle)
    config = copy.deepcopy(load_config("config/params.toml"))
    config["general"]["db_path"] = socle
    config["contributions"]["db_path"] = str(tmp_path / "contributions.db")
    config["contributions"]["store_dir"] = str(tmp_path / "images")
    return {"config": config, "tmp": tmp_path, "socle": socle}


def _make_client(env, **overrides):
    config = copy.deepcopy(env["config"])
    config["contributions"].update(overrides)
    app = create_app(config)
    app.testing = True
    return app


def _depot_data(image=None, **champs):
    data = {
        "fire_event_id": "1",
        "hotspot_raw_id": "1",
        "lat": str(P_LAT),
        "lon": str(P_LON),
        "consent": "1",
    }
    data.update(champs)
    data["image"] = (BytesIO(image if image is not None else _jpeg()), "photo.jpg")
    return data


def _store_files(env):
    d = Path(env["config"]["contributions"]["store_dir"])
    return sorted(d.glob("*.jpg")) if d.exists() else []


# --- dépôt nominal --------------------------------------------------------

def test_depot_ok_cree_ligne_soumise(env):
    app = _make_client(env)
    r = app.test_client().post(
        "/api/contrib/deposer", data=_depot_data(email="a@b.fr"),
        content_type="multipart/form-data",
    )
    assert r.status_code == 201
    body = r.get_json()
    assert body["statut"] == "soumise"
    assert body["code_insee"] == "33999"

    cc = connect_contrib(env["config"]["contributions"]["db_path"])
    row = cc.execute("SELECT * FROM contribution WHERE id=?", (body["id"],)).fetchone()
    cc.close()
    assert row["statut"] == "soumise"
    assert row["captured_at"] and row["consentement_at"] and row["created_at"]
    assert row["cgu_version"] == "2026-08"
    assert row["code_insee"] == "33999"
    assert row["email"] == "a@b.fr"
    assert row["ip_hash"] and len(row["ip_hash"]) == 64
    assert 0.0 <= row["distance_km"] <= 10.0
    # Deux images écrites (affichage + vignette), dimensions renseignées.
    assert Path(row["image_path"]).exists() and Path(row["thumb_path"]).exists()
    assert row["largeur"] and row["thumb_largeur"] <= 480
    assert len(_store_files(env)) == 2


def test_email_optionnel_absent_ok(env):
    app = _make_client(env)
    r = app.test_client().post(
        "/api/contrib/deposer", data=_depot_data(), content_type="multipart/form-data"
    )
    assert r.status_code == 201


# --- garde-fous -----------------------------------------------------------

def test_sans_consentement_400_rien_ecrit(env):
    app = _make_client(env)
    r = app.test_client().post(
        "/api/contrib/deposer", data=_depot_data(consent="0"),
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    assert _store_files(env) == []  # images écrites au dépôt UNIQUEMENT


def test_email_invalide_400(env):
    app = _make_client(env)
    r = app.test_client().post(
        "/api/contrib/deposer", data=_depot_data(email="pas-un-email"),
        content_type="multipart/form-data",
    )
    assert r.status_code == 400


def test_image_absente_400(env):
    app = _make_client(env)
    data = {"fire_event_id": "1", "hotspot_raw_id": "1", "lat": str(P_LAT),
            "lon": str(P_LON), "consent": "1"}
    r = app.test_client().post("/api/contrib/deposer", data=data,
                               content_type="multipart/form-data")
    assert r.status_code == 400


def test_image_illisible_400(env):
    app = _make_client(env)
    r = app.test_client().post(
        "/api/contrib/deposer", data=_depot_data(image=b"pas une image"),
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    assert _store_files(env) == []


def test_ancre_hors_rayon_422_rien_ecrit(env):
    """Géoloc à ~33 km du hotspot (mais dans le territoire) → refus, aucune image écrite."""
    app = _make_client(env)
    r = app.test_client().post(
        "/api/contrib/deposer",
        data=_depot_data(lat=str(P_LAT + 0.30)),
        content_type="multipart/form-data",
    )
    assert r.status_code == 422
    assert _store_files(env) == []


def test_hors_territoire_400(env):
    app = _make_client(env)
    r = app.test_client().post(
        "/api/contrib/deposer", data=_depot_data(lat="0", lon="0"),
        content_type="multipart/form-data",
    )
    assert r.status_code == 400


# --- anti-abus IP ---------------------------------------------------------

def test_ip_blocklistee_403(env):
    app = _make_client(env)  # migre la base contributions
    ip_h = hash_ip("127.0.0.1", SECRET)  # remote_addr par défaut du client de test
    cc = connect_contrib(env["config"]["contributions"]["db_path"])
    cc.execute(
        "INSERT INTO ip_blocklist (ip_hash, motif, source, cree_at) "
        "VALUES (?, 'test', 'manuel', '2026-08-07T00:00:00Z')",
        (ip_h,),
    )
    cc.commit()
    cc.close()

    r = app.test_client().post(
        "/api/contrib/deposer", data=_depot_data(), content_type="multipart/form-data"
    )
    assert r.status_code == 403
    assert _store_files(env) == []


def test_quota_atteint_429(env):
    app = _make_client(env, max_photos_ip_jour=1)
    client = app.test_client()
    r1 = client.post("/api/contrib/deposer", data=_depot_data(),
                     content_type="multipart/form-data")
    assert r1.status_code == 201
    # Seconde image (couleur différente → sha différent, pas un doublon) → quota dépassé.
    r2 = client.post(
        "/api/contrib/deposer", data=_depot_data(image=_jpeg(couleur=(10, 120, 200))),
        content_type="multipart/form-data",
    )
    assert r2.status_code == 429


# --- idempotence ----------------------------------------------------------

def test_doublon_sha256_idempotent(env):
    app = _make_client(env)
    client = app.test_client()
    img = _jpeg(couleur=(33, 66, 99))
    r1 = client.post("/api/contrib/deposer", data=_depot_data(image=img),
                     content_type="multipart/form-data")
    r2 = client.post("/api/contrib/deposer", data=_depot_data(image=img),
                     content_type="multipart/form-data")
    assert r1.status_code == 201
    assert r2.status_code == 200 and r2.get_json()["statut"] == "doublon"
    assert r2.get_json()["id"] == r1.get_json()["id"]

    cc = connect_contrib(env["config"]["contributions"]["db_path"])
    n = cc.execute("SELECT COUNT(*) AS n FROM contribution").fetchone()["n"]
    cc.close()
    assert n == 1  # une seule ligne malgré deux POST


# --- secret & plafond -----------------------------------------------------

def test_secret_manquant_503(env, monkeypatch):
    monkeypatch.delenv("CONTRIB_HASH_SECRET", raising=False)
    app = _make_client(env)
    r = app.test_client().post(
        "/api/contrib/deposer", data=_depot_data(), content_type="multipart/form-data"
    )
    assert r.status_code == 503


def test_upload_trop_gros_413_json(env):
    app = _make_client(env)
    app.config["MAX_CONTENT_LENGTH"] = 500  # plafond serré pour le test
    r = app.test_client().post(
        "/api/contrib/deposer", data=_depot_data(image=_jpeg(2000, 2000)),
        content_type="multipart/form-data",
    )
    assert r.status_code == 413
    assert r.get_json()["error"]
