"""Tests des transitions de modération (Spec 10 §6, étape 6b).

Publier (assigne public_id, notifie), rejeter (motif + purge), blacklister (rejet + IP
bornée), signaler (délistage), et l'invariant usage-unique-de-fait (re-clic = no-op).
"""

from __future__ import annotations

import pytest

from vigifeu.contrib.db import connect_contrib, migrate_contrib
from vigifeu.contrib.moderation import blacklister, publier, rejeter, signaler

T0 = "2026-08-07T10:00:00Z"


@pytest.fixture()
def cc(tmp_path):
    conn = connect_contrib(str(tmp_path / "contributions.db"))
    migrate_contrib(conn)
    yield conn
    conn.close()


def _ajout(cc, *, statut="a_moderer", email=None, ip_hash=None, sha="s", public_id=None) -> int:
    cur = cc.execute(
        "INSERT INTO contribution (captured_at, image_sha256, consentement_at, cgu_version, "
        "statut, email, ip_hash, public_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (T0, sha, T0, "2026-08", statut, email, ip_hash, public_id, T0),
    )
    cc.commit()
    return cur.lastrowid


def _row(cc, cid):
    return cc.execute("SELECT * FROM contribution WHERE id=?", (cid,)).fetchone()


# --- publier --------------------------------------------------------------

def test_publier_assigne_public_id_et_trace(cc):
    cid = _ajout(cc, email="a@b.fr")
    out = publier(cc, cid, par="mail", now=T0)
    assert out["applique"] is True
    assert out["public_id"] and out["email"] == "a@b.fr"

    r = _row(cc, cid)
    assert r["statut"] == "publiee"
    assert r["public_id"] == out["public_id"]
    assert r["publiee_at"] == T0 and r["moderee_par"] == "mail" and r["moderee_at"] == T0
    assert r["purge_prevue_at"] is None


def test_publier_sans_email_ok(cc):
    cid = _ajout(cc)
    out = publier(cc, cid, par="admin", now=T0)
    assert out["applique"] and out["email"] is None


def test_publier_reclic_noop(cc):
    """Lien mail rejoué : la 2e publication ne s'applique pas (usage unique de fait)."""
    cid = _ajout(cc)
    p1 = publier(cc, cid, par="mail", now=T0)
    p2 = publier(cc, cid, par="mail", now=T0)
    assert p1["applique"] and not p2["applique"]
    assert _row(cc, cid)["public_id"] == p1["public_id"]  # inchangé


def test_publier_sur_non_a_moderer_refuse(cc):
    cid = _ajout(cc, statut="soumise")
    assert publier(cc, cid, par="admin", now=T0)["applique"] is False
    assert _row(cc, cid)["statut"] == "soumise"


# --- rejeter --------------------------------------------------------------

def test_rejeter_pose_motif_et_purge(cc):
    cid = _ajout(cc)
    out = rejeter(cc, cid, par="mail", motif="hors sujet", now=T0, purge_mois=6)
    assert out["applique"] is True
    r = _row(cc, cid)
    assert r["statut"] == "rejetee" and r["motif_rejet"] == "hors sujet"
    assert r["moderee_par"] == "mail"
    assert r["purge_prevue_at"] == "2027-02-07T10:00:00Z"  # T0 + 6 mois


def test_rejeter_reclic_noop(cc):
    cid = _ajout(cc)
    assert rejeter(cc, cid, par="mail", motif="x", now=T0, purge_mois=6)["applique"]
    assert not rejeter(cc, cid, par="mail", motif="y", now=T0, purge_mois=6)["applique"]


# --- blacklister ----------------------------------------------------------

def test_blacklister_rejette_et_bloque_ip(cc):
    cid = _ajout(cc, ip_hash="abc123")
    out = blacklister(cc, cid, par="mail", now=T0, expire_jours=90, purge_mois=6)
    assert out == {"applique": True, "ip_bloquee": True}
    assert _row(cc, cid)["statut"] == "rejetee"
    bl = cc.execute("SELECT * FROM ip_blocklist WHERE ip_hash='abc123'").fetchone()
    assert bl["source"] == "manuel"
    assert bl["expire_at"] == "2026-11-05T10:00:00Z"  # T0 + 90 j


def test_blacklister_sans_ip_rejette_seulement(cc):
    cid = _ajout(cc, ip_hash=None)
    out = blacklister(cc, cid, par="admin", now=T0, expire_jours=90, purge_mois=6)
    assert out == {"applique": True, "ip_bloquee": False}
    assert _row(cc, cid)["statut"] == "rejetee"


def test_blacklister_reclic_noop(cc):
    cid = _ajout(cc, ip_hash="abc123")
    assert blacklister(cc, cid, par="mail", now=T0, expire_jours=90, purge_mois=6)["applique"]
    r2 = blacklister(cc, cid, par="mail", now=T0, expire_jours=90, purge_mois=6)
    assert r2["applique"] is False


# --- signaler -------------------------------------------------------------

def test_signaler_deliste_une_publiee(cc):
    cid = _ajout(cc, statut="publiee", public_id="pub-xyz")
    out = signaler(cc, "pub-xyz", now=T0)
    assert out["applique"] is True
    r = _row(cc, cid)
    assert r["statut"] == "a_moderer"          # délisté (widget = publiee only) + re-modération
    assert r["public_id"] == "pub-xyz"         # conservé


def test_signaler_public_id_inconnu_noop(cc):
    assert signaler(cc, "inexistant", now=T0)["applique"] is False


def test_signaler_non_publiee_noop(cc):
    cid = _ajout(cc, statut="a_moderer", public_id="pub-xyz")
    assert signaler(cc, "pub-xyz", now=T0)["applique"] is False
    assert _row(cc, cid)["statut"] == "a_moderer"
