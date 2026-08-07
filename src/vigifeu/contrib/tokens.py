"""Tokens d'action signés pour la modération par mail (Spec 10 §6).

Un lien d'action envoyé par mail (Publier / Rejeter / Blacklister) porte un **token signé
HMAC, par action, expirant**. Sans signature valide, aucune action : quiconque possède l'URL
ne peut rien forcer, et un token périmé est refusé.

Format compact `payload.signature`, tout en base64url sans padding :
- `payload` = JSON `{"cid": <int>, "a": <action>, "exp": <iso UTC>}` ;
- `signature` = HMAC-SHA256(payload, `CONTRIB_HASH_SECRET`).

⚠️ Le token **autorise**, il ne déclenche pas : le garde-fou GET-sans-effet / POST-agit et
l'usage-unique-de-fait (`statut = 'a_moderer'`) sont côté endpoint/transition (§6), pas ici.
La vérification est à **temps constant** (`hmac.compare_digest`) et l'expiration est comparée
en UTC.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from vigifeu.contrib.dates import now_iso, parse_iso, plus_heures

ACTIONS = ("publier", "rejeter", "blacklister")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _signer(payload_b64: str, secret: str) -> str:
    sig = hmac.new(secret.encode(), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64(sig)


def creer_token(cid: int, action: str, *, secret: str, ttl_h: int, now: str | None = None) -> str:
    """Fabrique un token signé pour (`cid`, `action`), valable `ttl_h` heures."""
    if action not in ACTIONS:
        raise ValueError(f"action inconnue : {action}")
    exp = plus_heures(now or now_iso(), ttl_h)
    payload = _b64(json.dumps({"cid": cid, "a": action, "exp": exp}).encode("utf-8"))
    return f"{payload}.{_signer(payload, secret)}"


def verifier_token(token: str, *, secret: str, now: str | None = None) -> dict | None:
    """Retourne `{"cid", "action"}` si signature valide ET non expiré, sinon `None`.

    Aucune exception vers l'appelant : un token malformé, mal signé ou périmé → `None`
    (le endpoint répond alors 400/410 sans divulguer la cause).
    """
    try:
        payload_b64, sig = token.split(".", 1)
    except (ValueError, AttributeError):
        return None
    if not hmac.compare_digest(sig, _signer(payload_b64, secret)):
        return None
    try:
        data = json.loads(_unb64(payload_b64))
        cid = int(data["cid"])
        action = data["a"]
        exp = data["exp"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if action not in ACTIONS:
        return None
    if parse_iso(exp) <= parse_iso(now or now_iso()):
        return None
    return {"cid": cid, "action": action}
