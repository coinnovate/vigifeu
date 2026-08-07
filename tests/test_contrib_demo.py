"""Tests du mode démo (Spec 10, hors-prod) — tester le parcours sans feu réel à proximité.

feux-proches synthétise un feu de test (cède au feu réel s'il y en a un), le dépôt en mode
démo passe direct en `a_moderer` (visible en admin) et déclenche le mail de modération.
"""

from __future__ import annotations

import base64
import copy
from io import BytesIO

import pytest
from PIL import Image

from vigifeu.contrib.app import create_app
from vigifeu.contrib.db import connect_contrib
from vigifeu.model.db import connect, load_config, migrate

SECRET = "secret-demo"
ADMIN = ("mod", "mdp")
MODEMAIL = "moderation@sentifeu.fr"
P_LAT, P_LON = 44.7500, -1.1000
T0 = "2026-08-07T00:00:00Z"


def _carre_wkt(lat, lon, demi=0.05):
    s, n, w, e = lat - demi, lat + demi, lon - demi, lon + demi
    return f"POLYGON(({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))"


def _seed_socle(path, *, avec_feu_reel=False):
    c = connect(path)
    migrate(c)
    c.execute("INSERT INTO commune (code_insee, slug, nom, geometry_wkt, centroid_lat, centroid_lon) "
              "VALUES ('33999','c','C',?,?,?)", (_carre_wkt(P_LAT, P_LON), P_LAT, P_LON))
    if avec_feu_reel:
        c.execute("INSERT INTO satellite_source (id, code, platform, instrument) "
                  "VALUES (1,'V','P','I')")
        c.execute("INSERT INTO ingestion_run (id, source, started_at, status) "
                  "VALUES (1,'t',?,'ok')", (T0,))
        c.execute("INSERT INTO fire_event (id, public_id, created_at, lifecycle) "
                  "VALUES (1,'2026-reel',?, 'actif')", (T0,))
        c.execute("INSERT INTO fire_event_version (id, fire_event_id, version_n, computed_at) "
                  "VALUES (1,1,1,?)", (T0,))
        c.execute("INSERT INTO hotspot_raw (id, source_id, lat, lon, acq_at, ingested_at, "
                  "ingestion_run_id) VALUES (1,1,?,?,?,?,1)", (P_LAT + 0.01, P_LON, T0, T0))
        c.execute("INSERT INTO fe_hotspot (fire_event_version_id, hotspot_id) VALUES (1,1)")
    c.commit()
    c.close()


class FauxMailer:
    def __init__(self):
        self.envoyes = []

    def envoyer(self, mail):
        self.envoyes.append(mail)


@pytest.fixture()
def make(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTRIB_HASH_SECRET", SECRET)
    monkeypatch.setenv("CONTRIB_ADMIN_USER", ADMIN[0])
    monkeypatch.setenv("CONTRIB_ADMIN_PASSWORD", ADMIN[1])
    monkeypatch.setenv("CONTRIB_MODERATION_EMAIL", MODEMAIL)
    monkeypatch.delenv("CONTRIB_SMTP_HOST", raising=False)

    def _factory(*, avec_feu_reel=False, mode_demo=True):
        socle = str(tmp_path / "socle.db")
        _seed_socle(socle, avec_feu_reel=avec_feu_reel)
        config = copy.deepcopy(load_config("config/params.toml"))
        config["general"]["db_path"] = socle
        config["contributions"]["db_path"] = str(tmp_path / "contributions.db")
        config["contributions"]["store_dir"] = str(tmp_path / "images")
        config["contributions"]["mode_demo"] = mode_demo
        app = create_app(config)
        app.testing = True
        app.config["CONTRIB_MAILER"] = FauxMailer()
        return app

    return _factory


def _jpeg(couleur=(200, 80, 40)):
    b = BytesIO()
    Image.new("RGB", (1000, 800), couleur).save(b, format="JPEG")
    return b.getvalue()


def _depot(app, image=None):
    data = {"fire_event_id": "0", "hotspot_raw_id": "0", "lat": str(P_LAT),
            "lon": str(P_LON), "consent": "1",
            "image": (BytesIO(image or _jpeg()), "photo.jpg")}
    return app.test_client().post("/api/contrib/deposer", data=data,
                                  content_type="multipart/form-data")


# --- feux-proches ---------------------------------------------------------

def test_feux_proches_demo_synthetique(make):
    app = make()
    feux = app.test_client().get(f"/api/contrib/feux-proches?lat={P_LAT}&lon={P_LON}").get_json()["feux"]
    assert feux == [{"fire_event_id": 0, "public_id": "demo-local",
                     "hotspot_raw_id": 0, "distance_km": 0.0}]


def test_demo_cede_au_feu_reel(make):
    """Si un vrai feu est proche, il prime — le feu de test n'est pas ajouté."""
    app = make(avec_feu_reel=True)
    feux = app.test_client().get(f"/api/contrib/feux-proches?lat={P_LAT}&lon={P_LON}").get_json()["feux"]
    assert [f["public_id"] for f in feux] == ["2026-reel"]


def test_pas_de_demo_hors_mode(make):
    """Hors mode démo, aucun feu à proximité = liste vide (comportement de prod)."""
    app = make(mode_demo=False)
    feux = app.test_client().get(f"/api/contrib/feux-proches?lat={P_LAT}&lon={P_LON}").get_json()["feux"]
    assert feux == []


# --- dépôt ----------------------------------------------------------------

def test_depot_demo_va_en_a_moderer(make):
    app = make()
    r = _depot(app)
    assert r.status_code == 201
    body = r.get_json()
    assert body["statut"] == "a_moderer"
    assert body["code_insee"] == "33999"

    cc = connect_contrib(app.config["VIGIFEU"]["contributions"]["db_path"])
    row = cc.execute("SELECT statut, fire_event_id FROM contribution WHERE id=?", (body["id"],)).fetchone()
    cc.close()
    assert row["statut"] == "a_moderer"
    assert row["fire_event_id"] is None  # pas rattaché à un vrai feu (démo)


def test_depot_demo_visible_en_admin(make):
    app = make()
    cid = _depot(app).get_json()["id"]
    b = base64.b64encode(f"{ADMIN[0]}:{ADMIN[1]}".encode()).decode()
    r = app.test_client().get("/admin/contrib", headers={"Authorization": f"Basic {b}"})
    assert r.status_code == 200 and f"#{cid}".encode() in r.data


def test_depot_demo_envoie_mail_moderation(make):
    app = make()
    _depot(app)
    envoyes = app.config["CONTRIB_MAILER"].envoyes
    assert len(envoyes) == 1
    assert envoyes[0].destinataire == MODEMAIL
    assert "/api/contrib/action/" in envoyes[0].html  # liens d'action signés
