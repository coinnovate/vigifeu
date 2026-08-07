"""Jobs planifiés du service contributif (Spec 10 §5/§9) — auto-filtre + purge.

Tournent dans un `BackgroundScheduler` démarré par `serveur.main` (mêmes process/threads que
waitress ; un seul worker → jobs sérialisés). Deux tâches :

- **auto-filtre** (`job_filtre`, cadence `filtre_intervalle_min`) : charge le moteur ONNX,
  traite un lot de `soumise`, et **envoie le mail de modération** pour chaque contribution
  passée en `a_moderer`. **Dégradation** : si les modèles ONNX ne sont pas déployés
  (`FiltreIndisponible`), le job ne fait rien — les contributions restent `soumise` (jamais
  publiées seules, §11) ;
- **purge** (`job_purge`, quotidien) : rétention RGPD/LCEN (§9).

`envoyer_mail_moderation` est **pur** (pas de dépendance Flask) : réutilisé par le job ET par
l'endpoint de dépôt en mode démo (app.py).
"""

from __future__ import annotations

import logging
import os
import sqlite3

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler

from vigifeu.contrib.db import connect_contrib, connect_socle_readonly
from vigifeu.contrib.filtre import filtrer_lot
from vigifeu.contrib.filtre_onnx import FiltreIndisponible, charger_classifieur
from vigifeu.contrib.mail import mail_moderation, mailer_depuis_env
from vigifeu.contrib.purge import purger
from vigifeu.contrib.tokens import creer_token

log = logging.getLogger("vigifeu.contrib")


def feu_public_id(config: dict, cc: sqlite3.Connection, cid: int) -> str | None:
    """public_id du feu socle rattaché à une contribution (best-effort ; None si démo/absent)."""
    r = cc.execute("SELECT fire_event_id FROM contribution WHERE id=?", (cid,)).fetchone()
    if not r or r["fire_event_id"] is None:
        return None
    try:
        sc = connect_socle_readonly(config["general"]["db_path"])
    except FileNotFoundError:
        return None
    try:
        f = sc.execute(
            "SELECT public_id FROM fire_event WHERE id=?", (r["fire_event_id"],)
        ).fetchone()
        return f["public_id"] if f else None
    finally:
        sc.close()


def envoyer_mail_moderation(
    config: dict, cc: sqlite3.Connection, cid: int, *, mailer, dest: str | None, secret: str | None
) -> bool:
    """Envoie le mail de modération (vignette + 3 liens signés) pour une contribution `a_moderer`.

    Pur (aucun Flask). No-op silencieux si mailer/destinataire/secret manquants ou ligne absente.
    Retourne True si un mail est effectivement parti.
    """
    if not (mailer and dest and secret):
        return False
    r = cc.execute(
        "SELECT thumb_path, captured_at, distance_km, score_nsfw, score_feu "
        "FROM contribution WHERE id=?", (cid,)
    ).fetchone()
    if r is None:
        return False
    vignette = b""
    if r["thumb_path"] and os.path.exists(r["thumb_path"]):
        with open(r["thumb_path"], "rb") as f:
            vignette = f.read()
    ttl = config["contributions"]["action_token_ttl_h"]
    tokens = {a: creer_token(cid, a, secret=secret, ttl_h=ttl)
              for a in ("publier", "rejeter", "blacklister")}
    try:
        mailer.envoyer(mail_moderation(
            destinataire=dest, base_url=config["generate"]["base_url"], tokens=tokens,
            vignette=vignette, feu_public_id=feu_public_id(config, cc, cid),
            captured_at=r["captured_at"], distance_km=r["distance_km"],
            score_nsfw=r["score_nsfw"], score_feu=r["score_feu"],
        ))
        return True
    except Exception:  # pragma: no cover - dépend du relais SMTP
        log.warning("mail de modération non envoyé (cid=%s)", cid, exc_info=True)
        return False


def job_filtre(config: dict) -> dict:
    """Traite un lot de `soumise` puis notifie les nouvelles `a_moderer`. Dégrade si ONNX absent."""
    cc = connect_contrib(config["contributions"]["db_path"])
    try:
        try:
            classifieur = charger_classifieur(config)
        except FiltreIndisponible as exc:
            log.info("auto-filtre indisponible (%s) — contributions restent soumise", exc)
            return {"indisponible": True}
        res = filtrer_lot(cc, config, classifieur, limite=config["contributions"]["filtre_lot_max"])
        if res["a_moderer_ids"]:
            mailer = mailer_depuis_env(config)
            dest = os.environ.get("CONTRIB_MODERATION_EMAIL")
            secret = os.environ.get("CONTRIB_HASH_SECRET")
            for cid in res["a_moderer_ids"]:
                envoyer_mail_moderation(config, cc, cid, mailer=mailer, dest=dest, secret=secret)
        log.info("auto-filtre: %s", {k: v for k, v in res.items() if k != "a_moderer_ids"})
        return res
    finally:
        cc.close()


def job_purge(config: dict) -> dict:
    """Purge quotidienne (rétention §9)."""
    cc = connect_contrib(config["contributions"]["db_path"])
    try:
        res = purger(cc, config)
        log.info("purge: %s", res)
        return res
    finally:
        cc.close()


def construire_planificateur(config: dict) -> BackgroundScheduler:
    """BackgroundScheduler (1 worker, jobs sérialisés) : auto-filtre périodique + purge nocturne."""
    cfg = config["contributions"]
    sched = BackgroundScheduler(
        timezone="UTC",
        executors={"default": ThreadPoolExecutor(max_workers=1)},  # 1 cœur (§5), pas de chevauchement
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
    )
    sched.add_job(lambda: job_filtre(config), "interval",
                  minutes=cfg["filtre_intervalle_min"], id="contrib_filtre")
    sched.add_job(lambda: job_purge(config), "cron", hour=3, minute=15, id="contrib_purge")
    return sched
