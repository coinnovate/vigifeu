"""Tests des endpoints de modération (Spec 10 §6, étape 6d).

Garde-fou anti-préchargement (GET confirme sans effet ; seul le POST mute), usage unique,
tokens falsifiés/périmés, notification de publication, signalement public, et page admin
(basic auth, actions, service authentifié des vignettes non publiées).
"""

from __future__ import annotations

import base64
import copy

import pytest

from vigifeu.contrib.app import create_app
from vigifeu.contrib.db import connect_contrib
from vigifeu.contrib.tokens import creer_token
from vigifeu.model.db import connect, load_config, migrate

SECRET = "secret-modération"
ADMIN = ("mod", "motdepasse")
T0 = "2026-08-07T10:00:00Z"


def _seed_socle(path):
    c = connect(path)
    migrate(c)
    c.execute(
        "INSERT INTO fire_event (id, public_id, created_at, lifecycle) "
        "VALUES (1, '2026-saumos', ?, 'actif')", (T0,)
    )
    c.commit()
    c.close()


class FauxMailer:
    def __init__(self):
        self.envoyes = []

    def envoyer(self, mail):
        self.envoyes.append(mail)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTRIB_HASH_SECRET", SECRET)
    monkeypatch.setenv("CONTRIB_ADMIN_USER", ADMIN[0])
    monkeypatch.setenv("CONTRIB_ADMIN_PASSWORD", ADMIN[1])
    monkeypatch.delenv("CONTRIB_SMTP_HOST", raising=False)
    socle = str(tmp_path / "socle.db")
    _seed_socle(socle)
    config = copy.deepcopy(load_config("config/params.toml"))
    config["general"]["db_path"] = socle
    config["contributions"]["db_path"] = str(tmp_path / "contributions.db")
    app = create_app(config)
    app.testing = True
    app.config["CONTRIB_MAILER"] = FauxMailer()
    app._tmp = tmp_path  # pratique pour les vignettes
    return app


def _ajout_a_moderer(app, *, email=None, ip_hash="ip1", sha="s1", fire_event_id=1) -> int:
    """Insère une contribution `a_moderer` avec une vignette sur disque. Retourne son id."""
    thumb = app._tmp / f"{sha}_thumb.jpg"
    thumb.write_bytes(b"\xff\xd8\xffvignette")
    cc = connect_contrib(app.config["VIGIFEU"]["contributions"]["db_path"])
    cur = cc.execute(
        "INSERT INTO contribution (fire_event_id, distance_km, captured_at, thumb_path, "
        "image_sha256, consentement_at, cgu_version, email, ip_hash, score_nsfw, score_feu, "
        "statut, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'a_moderer', ?)",
        (fire_event_id, 4.4, T0, str(thumb), sha, T0, "2026-08", email, ip_hash, 0.02, 0.88, T0),
    )
    cc.commit()
    cur_id = cur.lastrowid
    cc.close()
    return cur_id


def _statut(app, cid):
    cc = connect_contrib(app.config["VIGIFEU"]["contributions"]["db_path"])
    s = cc.execute("SELECT statut FROM contribution WHERE id=?", (cid,)).fetchone()["statut"]
    cc.close()
    return s


def _basic(creds):
    b = base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()
    return {"Authorization": f"Basic {b}"}


# --- garde-fou GET/POST ---------------------------------------------------

def test_get_action_confirme_sans_effet(app):
    cid = _ajout_a_moderer(app)
    tok = creer_token(cid, "publier", secret=SECRET, ttl_h=72)
    r = app.test_client().get(f"/api/contrib/action/{tok}")
    assert r.status_code == 200
    assert b"Confirmer" in r.data
    assert _statut(app, cid) == "a_moderer"  # AUCUN effet sur GET (préchargement inoffensif)


def test_post_action_publie_et_notifie(app):
    cid = _ajout_a_moderer(app, email="a@b.fr")
    tok = creer_token(cid, "publier", secret=SECRET, ttl_h=72)
    r = app.test_client().post(f"/api/contrib/action/{tok}")
    assert r.status_code == 200 and "appliquée" in r.get_data(as_text=True)
    assert _statut(app, cid) == "publiee"
    # Notification de publication envoyée au contributeur, avec lien du feu socle.
    envoyes = app.config["CONTRIB_MAILER"].envoyes
    assert len(envoyes) == 1 and envoyes[0].destinataire == "a@b.fr"
    assert "2026-saumos" in envoyes[0].html


def test_post_action_rejouee_noop(app):
    cid = _ajout_a_moderer(app, email="a@b.fr")
    tok = creer_token(cid, "publier", secret=SECRET, ttl_h=72)
    c = app.test_client()
    c.post(f"/api/contrib/action/{tok}")
    r2 = c.post(f"/api/contrib/action/{tok}")  # lien rejoué
    assert "Déjà traité" in r2.get_data(as_text=True)
    assert _statut(app, cid) == "publiee"
    assert len(app.config["CONTRIB_MAILER"].envoyes) == 1  # pas de 2e notif


def test_token_falsifie_410(app):
    cid = _ajout_a_moderer(app)
    tok = creer_token(cid, "publier", secret=SECRET, ttl_h=72)
    r = app.test_client().post(f"/api/contrib/action/{tok}xxx")
    assert r.status_code == 410
    assert _statut(app, cid) == "a_moderer"


def test_token_expire_410(app):
    cid = _ajout_a_moderer(app)
    # émis loin dans le passé → déjà expiré à la vérification (now réel).
    tok = creer_token(cid, "publier", secret=SECRET, ttl_h=1, now="2000-01-01T00:00:00Z")
    r = app.test_client().post(f"/api/contrib/action/{tok}")
    assert r.status_code == 410
    assert _statut(app, cid) == "a_moderer"


def test_post_rejeter_et_blacklister(app):
    cid_r = _ajout_a_moderer(app, sha="sr", ip_hash="ipr")
    cid_b = _ajout_a_moderer(app, sha="sb", ip_hash="ipb")
    c = app.test_client()
    c.post(f"/api/contrib/action/{creer_token(cid_r, 'rejeter', secret=SECRET, ttl_h=72)}")
    c.post(f"/api/contrib/action/{creer_token(cid_b, 'blacklister', secret=SECRET, ttl_h=72)}")
    assert _statut(app, cid_r) == "rejetee"
    assert _statut(app, cid_b) == "rejetee"
    cc = connect_contrib(app.config["VIGIFEU"]["contributions"]["db_path"])
    bloquee = cc.execute("SELECT 1 FROM ip_blocklist WHERE ip_hash='ipb'").fetchone()
    cc.close()
    assert bloquee is not None


# --- signalement ----------------------------------------------------------

def test_signaler_deliste(app):
    cid = _ajout_a_moderer(app)
    tok = creer_token(cid, "publier", secret=SECRET, ttl_h=72)
    app.test_client().post(f"/api/contrib/action/{tok}")
    cc = connect_contrib(app.config["VIGIFEU"]["contributions"]["db_path"])
    pub = cc.execute("SELECT public_id FROM contribution WHERE id=?", (cid,)).fetchone()["public_id"]
    cc.close()

    r = app.test_client().post("/api/contrib/signaler", data={"public_id": pub})
    assert r.status_code == 200
    assert _statut(app, cid) == "a_moderer"  # délisté + remis en file


def test_signaler_inconnu_reponse_neutre(app):
    r = app.test_client().post("/api/contrib/signaler", data={"public_id": "inexistant"})
    assert r.status_code == 200  # pas d'énumération : même réponse que pour un id valide


# --- page admin -----------------------------------------------------------

def test_admin_sans_auth_401(app):
    assert app.test_client().get("/admin/contrib").status_code == 401


def test_admin_mauvais_mdp_401(app):
    r = app.test_client().get("/admin/contrib", headers=_basic(("mod", "faux")))
    assert r.status_code == 401


def test_admin_liste_a_moderer(app):
    cid = _ajout_a_moderer(app)
    r = app.test_client().get("/admin/contrib", headers=_basic(ADMIN))
    assert r.status_code == 200
    assert f"#{cid}".encode() in r.data


def test_admin_action_publie(app):
    cid = _ajout_a_moderer(app, email="a@b.fr")
    r = app.test_client().post(
        "/admin/contrib/action", data={"cid": cid, "action": "publier"},
        headers=_basic(ADMIN),
    )
    assert r.status_code == 200
    assert _statut(app, cid) == "publiee"
    assert len(app.config["CONTRIB_MAILER"].envoyes) == 1


def test_admin_photo_auth_et_jpeg(app):
    cid = _ajout_a_moderer(app)
    # Sans auth : refusé.
    assert app.test_client().get(f"/admin/contrib/photo/{cid}").status_code == 401
    # Avec auth : sert la vignette non publiée.
    r = app.test_client().get(f"/admin/contrib/photo/{cid}", headers=_basic(ADMIN))
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert r.data == b"\xff\xd8\xffvignette"


def test_admin_non_configure_503(app, monkeypatch):
    app.config["CONTRIB_ADMIN_USER"] = None  # simule admin non configurée
    r = app.test_client().get("/admin/contrib", headers=_basic(ADMIN))
    assert r.status_code == 503
