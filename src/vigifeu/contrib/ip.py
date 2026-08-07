"""Anti-abus par IP (Spec 10 §8) — hachage salé, blocklist, quota.

L'IP n'est **jamais conservée en clair** : on ne stocke qu'un HMAC-SHA256 salé
(`CONTRIB_HASH_SECRET`), suffisant pour compter/bloquer sans réidentifier (RGPD, §11).
Deux garde-fous au dépôt (§4) :

- **blocklist** — `ip_blocklist` (manuelle ou auto), blocage **borné** (`expire_at`) ;
- **quota** — au plus `max_photos_ip_jour` dépôts par IP sur 24 h glissantes (tout statut
  compte : une contribution auto-rejetée pèse aussi, c'est de l'anti-flood).

Derrière le reverse proxy, l'IP client est le **premier** maillon de `X-Forwarded-For`
(le proxy l'ajoute) ; à défaut `remote_addr`. On ne fait pas confiance aux maillons
suivants (spoofables).
"""

from __future__ import annotations

import hashlib
import hmac
import sqlite3


def client_ip(remote_addr: str | None, forwarded_for: str | None) -> str:
    """IP client : premier maillon de X-Forwarded-For (posé par le proxy) sinon remote_addr."""
    if forwarded_for:
        premier = forwarded_for.split(",")[0].strip()
        if premier:
            return premier
    return remote_addr or ""


def hash_ip(ip: str, secret: str) -> str:
    """HMAC-SHA256 salé de l'IP (jamais l'IP en clair). `secret` = `CONTRIB_HASH_SECRET`."""
    return hmac.new(secret.encode(), ip.encode(), hashlib.sha256).hexdigest()


def ip_bloquee(conn: sqlite3.Connection, ip_hash: str, *, now_iso: str) -> bool:
    """True si l'IP est sur la blocklist et le blocage n'a pas expiré (`expire_at` NULL = illimité)."""
    row = conn.execute(
        "SELECT 1 FROM ip_blocklist "
        "WHERE ip_hash = ? AND (expire_at IS NULL OR expire_at > ?) LIMIT 1",
        (ip_hash, now_iso),
    ).fetchone()
    return row is not None


def quota_atteint(
    conn: sqlite3.Connection, ip_hash: str, *, max_jour: int, depuis_iso: str
) -> bool:
    """True si l'IP a déjà `max_jour` dépôts depuis `depuis_iso` (fenêtre 24 h, tout statut)."""
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM contribution WHERE ip_hash = ? AND created_at >= ?",
        (ip_hash, depuis_iso),
    ).fetchone()["n"]
    return n >= max_jour
