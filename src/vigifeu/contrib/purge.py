"""Rétention & purge (Spec 10 §9) — job quotidien, idempotent, RGPD + LCEN conciliés.

Trois volets, tous rejouables sans effet de bord :

- **Rejetées échues** (`rejetee`/`auto_rejetee` dont `purge_prevue_at ≤ now`) : destruction
  effective des fichiers image + mise à NULL de `image_path`, `thumb_path`, `email`,
  `ip_hash` ; on **conserve le squelette non-perso** (`image_sha256`, `motif_rejet`, dates,
  `moderee_par`) → preuve de retrait (LCEN) et hash non re-soumissible (§3.4). `statut → purgee`.
- **Publiées** : conservées durablement ; seul l'`email` est purgé passé
  `purge_email_publiee_mois`. Image + squelette restent (archive datée, §7.5).
- **`ip_blocklist`** : les blocages expirés (`expire_at ≤ now`) sont supprimés (minimisation).

`purge_prevue_at` (posé à la décision, étapes 5/6) sert de déclencheur : pas de recalcul
d'échéance ici. Retourne un récapitulatif chiffré (à consigner par l'appelant).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from vigifeu.contrib.dates import now_iso, plus_mois


def purger(cc: sqlite3.Connection, config: dict, *, now: str | None = None) -> dict:
    """Exécute la purge complète. Idempotent : un 2e passage ne change rien de plus."""
    cfg = config["contributions"]
    now = now or now_iso()
    res = {
        "rejetees_purgees": 0,
        "fichiers_supprimes": 0,
        "emails_publiees_purges": 0,
        "blocklist_expiree_supprimee": 0,
    }

    # 1. Rejetées / auto-rejetées échues → purgee (fichiers détruits, colonnes perso nettoyées).
    rows = cc.execute(
        "SELECT id, image_path, thumb_path FROM contribution "
        "WHERE statut IN ('rejetee','auto_rejetee') "
        "AND purge_prevue_at IS NOT NULL AND purge_prevue_at <= ?",
        (now,),
    ).fetchall()
    for r in rows:
        for chemin in (r["image_path"], r["thumb_path"]):
            if chemin and Path(chemin).exists():
                Path(chemin).unlink()
                res["fichiers_supprimes"] += 1
        cc.execute(
            "UPDATE contribution SET statut='purgee', image_path=NULL, thumb_path=NULL, "
            "email=NULL, ip_hash=NULL, purgee_at=? WHERE id=?",
            (now, r["id"]),
        )
        res["rejetees_purgees"] += 1

    # 2. Email des publiées passé le délai (image + squelette conservés).
    cutoff_email = plus_mois(now, -int(cfg["purge_email_publiee_mois"]))
    cur = cc.execute(
        "UPDATE contribution SET email=NULL WHERE statut='publiee' AND email IS NOT NULL "
        "AND publiee_at IS NOT NULL AND publiee_at <= ?",
        (cutoff_email,),
    )
    res["emails_publiees_purges"] = cur.rowcount

    # 3. Blocages IP expirés.
    cur = cc.execute(
        "DELETE FROM ip_blocklist WHERE expire_at IS NOT NULL AND expire_at <= ?", (now,)
    )
    res["blocklist_expiree_supprimee"] = cur.rowcount

    cc.commit()
    return res
