"""Tests de la purge (Spec 10 §9, étape 7).

Rejetées échues → purgee (fichiers détruits, perso nettoyé, squelette conservé), rejetées
non échues intactes, email des publiées purgé au délai (image conservée), blocklist expirée
supprimée, et idempotence.
"""

from __future__ import annotations

import copy

import pytest

from vigifeu.contrib.dates import plus_mois
from vigifeu.contrib.db import connect_contrib, migrate_contrib
from vigifeu.contrib.purge import purger
from vigifeu.model.db import load_config

NOW = "2026-08-07T10:00:00Z"


@pytest.fixture()
def cc(tmp_path):
    conn = connect_contrib(str(tmp_path / "contributions.db"))
    migrate_contrib(conn)
    yield conn
    conn.close()


@pytest.fixture()
def config():
    return copy.deepcopy(load_config("config/params.toml"))


def _fichiers(tmp_path, sha):
    img = tmp_path / f"{sha}.jpg"
    thumb = tmp_path / f"{sha}_thumb.jpg"
    img.write_bytes(b"img")
    thumb.write_bytes(b"thumb")
    return img, thumb


def _row(cc, cid):
    return cc.execute("SELECT * FROM contribution WHERE id=?", (cid,)).fetchone()


def _ajout(cc, *, statut, sha, image_path=None, thumb_path=None, email=None, ip_hash=None,
           motif_rejet=None, purge_prevue_at=None, publiee_at=None, moderee_par=None) -> int:
    cur = cc.execute(
        "INSERT INTO contribution (captured_at, image_path, thumb_path, image_sha256, "
        "consentement_at, cgu_version, statut, email, ip_hash, motif_rejet, purge_prevue_at, "
        "publiee_at, moderee_par, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (NOW, image_path, thumb_path, sha, NOW, "2026-08", statut, email, ip_hash,
         motif_rejet, purge_prevue_at, publiee_at, moderee_par, NOW),
    )
    cc.commit()
    return cur.lastrowid


# --- rejetées -------------------------------------------------------------

def test_rejetee_echue_purgee_squelette_conserve(cc, config, tmp_path):
    img, thumb = _fichiers(tmp_path, "sr")
    cid = _ajout(
        cc, statut="rejetee", sha="sr", image_path=str(img), thumb_path=str(thumb),
        email="a@b.fr", ip_hash="ip1", motif_rejet="hors sujet", moderee_par="mail",
        purge_prevue_at="2026-08-01T00:00:00Z",  # échue (< now)
    )
    res = purger(cc, config, now=NOW)

    assert res["rejetees_purgees"] == 1 and res["fichiers_supprimes"] == 2
    assert not img.exists() and not thumb.exists()          # destruction effective
    r = _row(cc, cid)
    assert r["statut"] == "purgee"
    assert r["image_path"] is None and r["thumb_path"] is None
    assert r["email"] is None and r["ip_hash"] is None      # perso détruit
    assert r["purgee_at"] == NOW
    # Squelette non-perso conservé (preuve LCEN + hash non re-soumissible).
    assert r["image_sha256"] == "sr" and r["motif_rejet"] == "hors sujet"
    assert r["moderee_par"] == "mail"


def test_auto_rejetee_echue_purgee(cc, config, tmp_path):
    img, thumb = _fichiers(tmp_path, "sa")
    cid = _ajout(cc, statut="auto_rejetee", sha="sa", image_path=str(img), thumb_path=str(thumb),
                 purge_prevue_at="2026-01-01T00:00:00Z")
    purger(cc, config, now=NOW)
    assert _row(cc, cid)["statut"] == "purgee"


def test_rejetee_non_echue_intacte(cc, config, tmp_path):
    img, thumb = _fichiers(tmp_path, "sf")
    cid = _ajout(cc, statut="rejetee", sha="sf", image_path=str(img), thumb_path=str(thumb),
                 email="a@b.fr", purge_prevue_at="2027-01-01T00:00:00Z")  # future
    res = purger(cc, config, now=NOW)
    assert res["rejetees_purgees"] == 0
    assert img.exists() and thumb.exists()
    assert _row(cc, cid)["statut"] == "rejetee" and _row(cc, cid)["email"] == "a@b.fr"


# --- publiées -------------------------------------------------------------

def test_email_publiee_purge_apres_delai(cc, config, tmp_path):
    img, _ = _fichiers(tmp_path, "sp")
    # publiée il y a > 3 mois → email purgé, image conservée.
    vieux = plus_mois(NOW, -4)
    cid = _ajout(cc, statut="publiee", sha="sp", image_path=str(img), email="a@b.fr",
                 publiee_at=vieux)
    res = purger(cc, config, now=NOW)
    assert res["emails_publiees_purges"] == 1
    r = _row(cc, cid)
    assert r["email"] is None
    assert r["statut"] == "publiee" and r["image_path"] == str(img)  # conservée durablement
    assert img.exists()


def test_email_publiee_recente_conserve(cc, config):
    recent = plus_mois(NOW, -1)  # < 3 mois
    cid = _ajout(cc, statut="publiee", sha="spr", email="a@b.fr", publiee_at=recent)
    purger(cc, config, now=NOW)
    assert _row(cc, cid)["email"] == "a@b.fr"


# --- blocklist ------------------------------------------------------------

def test_blocklist_expiree_supprimee_active_conservee(cc, config):
    cc.execute(
        "INSERT INTO ip_blocklist (ip_hash, source, cree_at, expire_at) VALUES "
        "('vieux', 'auto', ?, '2026-07-01T00:00:00Z'), "  # expiré
        "('actif', 'manuel', ?, '2027-01-01T00:00:00Z')",  # encore actif
        (NOW, NOW),
    )
    cc.commit()
    res = purger(cc, config, now=NOW)
    assert res["blocklist_expiree_supprimee"] == 1
    restants = {r["ip_hash"] for r in cc.execute("SELECT ip_hash FROM ip_blocklist").fetchall()}
    assert restants == {"actif"}


# --- idempotence ----------------------------------------------------------

def test_idempotent(cc, config, tmp_path):
    img, thumb = _fichiers(tmp_path, "si")
    _ajout(cc, statut="rejetee", sha="si", image_path=str(img), thumb_path=str(thumb),
           email="a@b.fr", purge_prevue_at="2026-01-01T00:00:00Z")
    r1 = purger(cc, config, now=NOW)
    r2 = purger(cc, config, now=NOW)  # 2e passage : plus rien à faire, aucune erreur
    assert r1["rejetees_purgees"] == 1
    assert r2 == {"rejetees_purgees": 0, "fichiers_supprimes": 0,
                  "emails_publiees_purges": 0, "blocklist_expiree_supprimee": 0}
