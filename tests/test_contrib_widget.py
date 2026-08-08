"""Tests des endpoints d'exposition — widget (Spec 10 §7, étape 8).

JSON feu/commune (publiee only, tri captured_at desc, pagination, bloc feu, cache court),
service d'images (affichage + vignette, cache immutable), retrait → délisté + 410, non
énumérabilité (public_id opaque, id interne jamais exposé), et service du snippet.
"""

from __future__ import annotations

import copy

import pytest

from vigifeu.contrib.app import create_app
from vigifeu.contrib.db import connect_contrib
from vigifeu.model.db import connect, load_config, migrate

T = "2026-08-07T"


def _seed_socle(path):
    c = connect(path)
    migrate(c)
    for fid, pub in ((1, "2026-saumos"), (2, "2026-autre")):
        c.execute(
            "INSERT INTO fire_event (id, public_id, created_at, lifecycle) VALUES (?,?,?, 'actif')",
            (fid, pub, T + "00:00:00Z"),
        )
    c.commit()
    c.close()


@pytest.fixture()
def make(tmp_path):
    socle = str(tmp_path / "socle.db")
    _seed_socle(socle)

    def _factory(**overrides):
        config = copy.deepcopy(load_config("config/params.toml"))
        config["general"]["db_path"] = socle
        config["contributions"]["db_path"] = str(tmp_path / "contributions.db")
        config["contributions"].update(overrides)
        app = create_app(config)
        app.testing = True
        app._tmp = tmp_path
        return app

    return _factory


def _ajout(app, *, public_id, heure, fire_event_id=1, code_insee="33999",
           statut="publiee", sha=None):
    sha = sha or public_id
    img = app._tmp / f"{sha}.jpg"
    thumb = app._tmp / f"{sha}_thumb.jpg"
    img.write_bytes(b"\xff\xd8\xffIMG")
    thumb.write_bytes(b"\xff\xd8\xffTHM")
    cc = connect_contrib(app.config["VIGIFEU"]["contributions"]["db_path"])
    cc.execute(
        "INSERT INTO contribution (public_id, fire_event_id, captured_at, image_path, "
        "thumb_path, image_sha256, largeur, hauteur, thumb_largeur, thumb_hauteur, "
        "consentement_at, cgu_version, code_insee, statut, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (public_id, fire_event_id, T + heure, str(img), str(thumb), sha,
         1600, 1200, 480, 360, T + "00:00:00Z", "2026-08", code_insee, statut, T + "00:00:00Z"),
    )
    cc.commit()
    cc.close()


# --- JSON feu -------------------------------------------------------------

def test_photos_feu_publiee_only_et_tri(make):
    app = make()
    _ajout(app, public_id="pa", heure="10:00:00Z")
    _ajout(app, public_id="pb", heure="12:00:00Z")           # plus récent
    _ajout(app, public_id="pm", heure="11:00:00Z", statut="a_moderer")  # non publié → exclu
    r = app.test_client().get("/api/contrib/feu/2026-saumos/photos")
    assert r.status_code == 200
    data = r.get_json()
    assert data["total"] == 2
    assert [p["public_id"] for p in data["photos"]] == ["pb", "pa"]  # captured_at desc
    p = data["photos"][0]
    assert p["url"] == "/api/contrib/img/pb"
    assert p["thumb_url"] == "/api/contrib/img/pb?t=thumb"
    assert p["feu"]["public_id"] == "2026-saumos"
    assert (p["largeur"], p["thumb_hauteur"]) == (1600, 360)
    # Cache court pour propager vite publications/retraits.
    assert "max-age=60" in r.headers["Cache-Control"]


def test_photos_feu_pagination(make):
    app = make(photos_page_taille=2)
    for i, h in enumerate(("10:00:00Z", "11:00:00Z", "12:00:00Z")):
        _ajout(app, public_id=f"p{i}", heure=h)
    c = app.test_client()
    p1 = c.get("/api/contrib/feu/2026-saumos/photos?page=1").get_json()
    p2 = c.get("/api/contrib/feu/2026-saumos/photos?page=2").get_json()
    assert p1["total"] == 3 and len(p1["photos"]) == 2
    assert p2["page"] == 2 and len(p2["photos"]) == 1


def test_photos_feu_inconnu_vide(make):
    app = make()
    data = app.test_client().get("/api/contrib/feu/feu-inexistant/photos").get_json()
    assert data == {"total": 0, "page": 1, "photos": []}


def test_id_interne_jamais_expose(make):
    """Le JSON n'expose que public_id — jamais l'id auto-incrémenté (non énumérable)."""
    app = make()
    _ajout(app, public_id="pa", heure="10:00:00Z")
    p = app.test_client().get("/api/contrib/feu/2026-saumos/photos").get_json()["photos"][0]
    assert "id" not in p and set(p.keys()) >= {"public_id", "url", "thumb_url"}


# --- JSON commune ---------------------------------------------------------

def test_photos_commune_agrege_les_feux(make):
    app = make()
    _ajout(app, public_id="pa", heure="10:00:00Z", fire_event_id=1, code_insee="33999")
    _ajout(app, public_id="pb", heure="11:00:00Z", fire_event_id=2, code_insee="33999")
    _ajout(app, public_id="pc", heure="12:00:00Z", fire_event_id=1, code_insee="33000")  # autre commune
    data = app.test_client().get("/api/contrib/commune/33999/photos").get_json()
    assert data["total"] == 2
    feux = {p["public_id"]: p["feu"]["public_id"] for p in data["photos"]}
    assert feux == {"pa": "2026-saumos", "pb": "2026-autre"}  # chaque photo liée à SON feu


# --- service d'images -----------------------------------------------------

def test_image_affichage_et_vignette(make):
    app = make()
    _ajout(app, public_id="pa", heure="10:00:00Z")
    c = app.test_client()
    aff = c.get("/api/contrib/img/pa")
    thm = c.get("/api/contrib/img/pa?t=thumb")
    assert aff.status_code == 200 and aff.mimetype == "image/jpeg" and aff.data == b"\xff\xd8\xffIMG"
    assert thm.data == b"\xff\xd8\xffTHM"
    assert "immutable" in aff.headers["Cache-Control"] and "max-age=3600" in aff.headers["Cache-Control"]


def test_image_inconnue_404(make):
    app = make()
    assert app.test_client().get("/api/contrib/img/jamais-vu").status_code == 404


def test_retrait_deliste_et_410(make):
    """publiee → rejetee : sort du JSON ET la route image renvoie 410 (obligation hébergeur)."""
    app = make()
    _ajout(app, public_id="pa", heure="10:00:00Z")
    c = app.test_client()
    assert c.get("/api/contrib/img/pa").status_code == 200
    # Dépublication (signalement retenu) — public_id conservé.
    cc = connect_contrib(app.config["VIGIFEU"]["contributions"]["db_path"])
    cc.execute("UPDATE contribution SET statut='rejetee' WHERE public_id='pa'")
    cc.commit()
    cc.close()
    assert c.get("/api/contrib/img/pa").status_code == 410            # délistage hébergeur
    assert c.get("/api/contrib/feu/2026-saumos/photos").get_json()["total"] == 0  # hors JSON


# --- snippet --------------------------------------------------------------

def test_widget_js_servi(make):
    app = make()
    r = app.test_client().get("/api/contrib/widget.js")
    assert r.status_code == 200
    assert "javascript" in r.mimetype
    assert b"SentifeuPhotos" in r.data


def test_depot_js_servi(make):
    app = make()
    r = app.test_client().get("/api/contrib/depot.js")
    assert r.status_code == 200
    assert "javascript" in r.mimetype
    assert b"SentifeuDepot" in r.data
    assert b"getUserMedia" in r.data  # capture in-app stricte
    assert b"estMobile" in r.data     # détection PC → invitation mobile (§0)
    assert b"sentifeu-flottant" in r.data  # bouton flottant mobile (discoverabilité)
    assert b"112" in r.data           # bandeau sécurité (pas un service de secours)
    assert "licence non exclusive".encode() in r.data  # consentement = licence d'affichage


def test_asset_js_inconnu_404(make):
    app = make()
    assert app.test_client().get("/api/contrib/inexistant.js").status_code == 404
