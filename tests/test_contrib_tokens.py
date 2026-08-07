"""Tests des tokens d'action signés (Spec 10 §6, étape 6a).

Aller-retour valide, refus des tokens falsifiés / mauvais secret / périmés / malformés,
et liaison stricte au couple (cid, action).
"""

from __future__ import annotations

import pytest

from vigifeu.contrib.tokens import creer_token, verifier_token

SECRET = "secret-modération"
T0 = "2026-08-07T10:00:00Z"


def test_aller_retour_valide():
    tok = creer_token(42, "publier", secret=SECRET, ttl_h=72, now=T0)
    out = verifier_token(tok, secret=SECRET, now=T0)
    assert out == {"cid": 42, "action": "publier"}


def test_toutes_les_actions():
    for a in ("publier", "rejeter", "blacklister"):
        tok = creer_token(1, a, secret=SECRET, ttl_h=1, now=T0)
        assert verifier_token(tok, secret=SECRET, now=T0)["action"] == a


def test_action_inconnue_refusee_a_la_creation():
    with pytest.raises(ValueError):
        creer_token(1, "supprimer", secret=SECRET, ttl_h=1, now=T0)


def test_mauvais_secret_rejete():
    tok = creer_token(42, "publier", secret=SECRET, ttl_h=72, now=T0)
    assert verifier_token(tok, secret="autre", now=T0) is None


def test_payload_falsifie_rejete():
    """Modifier le cid dans le payload invalide la signature."""
    tok = creer_token(42, "rejeter", secret=SECRET, ttl_h=72, now=T0)
    payload, sig = tok.split(".", 1)
    import base64
    import json

    data = json.loads(base64.urlsafe_b64decode(payload + "=="))
    data["cid"] = 999
    faux_payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    assert verifier_token(f"{faux_payload}.{sig}", secret=SECRET, now=T0) is None


def test_expiration_rejetee():
    tok = creer_token(42, "publier", secret=SECRET, ttl_h=72, now=T0)
    # 72 h + 1 s après l'émission → expiré.
    assert verifier_token(tok, secret=SECRET, now="2026-08-10T10:00:01Z") is None
    # juste avant l'échéance → encore valide.
    assert verifier_token(tok, secret=SECRET, now="2026-08-10T09:59:59Z") is not None


def test_malforme_rejete():
    for mauvais in ("", "sans-point", "a.b.c", "x.y"):
        assert verifier_token(mauvais, secret=SECRET, now=T0) is None
