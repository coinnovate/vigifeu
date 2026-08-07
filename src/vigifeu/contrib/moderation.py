"""Transitions de modération (Spec 10 §6) — publier / rejeter / blacklister / signaler.

Logique pure sur la base contributions (aucun mail, aucun HTTP ici : le endpoint orchestre).
Deux invariants portés par toutes les transitions humaines :

- **usage unique de fait** — l'action ne s'applique que si `statut = 'a_moderer'`
  (`WHERE statut='a_moderer'`) : un re-clic (lien mail rejoué) est un no-op (`applique=False`) ;
- **traçabilité LCEN** — `moderee_par` (`admin`/`mail`), `moderee_at`, et `motif_rejet` sur les
  refus sont toujours renseignés.

`public_id` (URL publique de l'image) n'est assigné **qu'à la publication**, opaque et
**non énumérable** (`secrets`), unique (index socle) — un ré-essai couvre la collision
astronomiquement rare.
"""

from __future__ import annotations

import secrets
import sqlite3

from vigifeu.contrib.dates import now_iso, plus_heures, plus_mois


def _nouveau_public_id() -> str:
    """Identifiant public opaque, non énumérable (URL de l'image après publication)."""
    return secrets.token_urlsafe(9)


def publier(cc: sqlite3.Connection, cid: int, *, par: str, now: str | None = None) -> dict:
    """`a_moderer → publiee` : assigne `public_id`, `publiee_at`, trace la décision.

    Retourne `{applique, public_id, email}`. `email` (si fourni au dépôt) sert au endpoint
    à notifier la publication (§6). `applique=False` si la contribution n'est plus `a_moderer`.
    """
    now = now or now_iso()
    row = cc.execute(
        "SELECT statut, email FROM contribution WHERE id=?", (cid,)
    ).fetchone()
    if row is None or row["statut"] != "a_moderer":
        return {"applique": False, "public_id": None, "email": None}

    # Ré-essai en cas de collision d'index sur public_id (quasi impossible).
    for _ in range(5):
        pub = _nouveau_public_id()
        try:
            cur = cc.execute(
                "UPDATE contribution SET statut='publiee', public_id=?, publiee_at=?, "
                "moderee_par=?, moderee_at=?, purge_prevue_at=NULL "
                "WHERE id=? AND statut='a_moderer'",
                (pub, now, par, now, cid),
            )
        except sqlite3.IntegrityError:
            continue
        if cur.rowcount == 0:
            return {"applique": False, "public_id": None, "email": None}
        cc.commit()
        return {"applique": True, "public_id": pub, "email": row["email"]}
    return {"applique": False, "public_id": None, "email": None}


def rejeter(
    cc: sqlite3.Connection,
    cid: int,
    *,
    par: str,
    motif: str,
    now: str | None = None,
    purge_mois: int,
) -> dict:
    """`a_moderer → rejetee` : motif, traçabilité, échéance de purge (§9). No-op si déjà tranchée."""
    now = now or now_iso()
    cur = cc.execute(
        "UPDATE contribution SET statut='rejetee', motif_rejet=?, moderee_par=?, "
        "moderee_at=?, purge_prevue_at=? WHERE id=? AND statut='a_moderer'",
        (motif, par, now, plus_mois(now, purge_mois), cid),
    )
    cc.commit()
    return {"applique": cur.rowcount == 1}


def blacklister(
    cc: sqlite3.Connection,
    cid: int,
    *,
    par: str,
    now: str | None = None,
    expire_jours: int,
    purge_mois: int,
    motif: str = "blacklist IP",
) -> dict:
    """Rejette la contribution ET blackliste son IP (blocage borné, §8). No-op si plus `a_moderer`.

    L'ordre importe : on ne blackliste l'IP que si le rejet s'applique vraiment (garde l'action
    idempotente sur re-clic). `expire_at = now + expire_jours` (blocage révisable, jamais définitif).
    """
    now = now or now_iso()
    ip_row = cc.execute("SELECT ip_hash FROM contribution WHERE id=?", (cid,)).fetchone()
    rej = rejeter(cc, cid, par=par, motif=motif, now=now, purge_mois=purge_mois)
    if not rej["applique"]:
        return {"applique": False, "ip_bloquee": False}

    ip_bloquee = False
    if ip_row and ip_row["ip_hash"]:
        expire_at = plus_heures(now, 24 * expire_jours)  # blocage borné (§8)
        cc.execute(
            "INSERT INTO ip_blocklist (ip_hash, motif, source, cree_at, expire_at) "
            "VALUES (?,?, 'manuel', ?, ?) "
            "ON CONFLICT(ip_hash) DO UPDATE SET expire_at=excluded.expire_at, "
            "motif=excluded.motif, source='manuel'",
            (ip_row["ip_hash"], motif, now, expire_at),
        )
        cc.commit()
        ip_bloquee = True
    return {"applique": True, "ip_bloquee": ip_bloquee}


def signaler(cc: sqlite3.Connection, public_id: str, *, now: str | None = None) -> dict:
    """Signalement public (LCEN, §6) : `publiee → a_moderer` → **délisté immédiatement** + re-modération.

    Retire aussitôt la photo du widget (seules les `publiee` s'affichent) et la remet dans la
    file humaine. No-op si le `public_id` n'est pas (ou plus) publié. Le `public_id` est conservé
    (ré-publication éventuelle sous la même URL).
    """
    now = now or now_iso()
    cur = cc.execute(
        "UPDATE contribution SET statut='a_moderer', moderee_at=? "
        "WHERE public_id=? AND statut='publiee'",
        (now, public_id),
    )
    cc.commit()
    return {"applique": cur.rowcount == 1}
