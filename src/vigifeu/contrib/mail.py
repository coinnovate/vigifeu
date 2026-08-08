"""Infra mail du canal contributif (Spec 10 §6) — `smtplib` stdlib, zéro dépendance.

Infra **nouvelle** dans le projet (jusqu'ici : uniquement des pings healthchecks.io). Deux
mails :

- **modération** — à chaque passage en `a_moderer`, envoyé au modérateur : vignette inline,
  détails (feu, `captured_at`, distance, scores auto) et **trois liens d'action signés**
  (Publier / Rejeter / Blacklister) pointant vers `GET /api/contrib/action/{token}` ;
- **notification de publication** — au contributeur (si `email` fourni), une fois la photo
  publiée.

La **construction** des messages est pure et testée (`mail_moderation`/`mail_publication`) ;
l'**envoi** SMTP est un adaptateur mince (`MailerSMTP`) injecté via le protocole `Mailer`
(les tests injectent un faux). `mailer_depuis_env` retourne `None` si le SMTP n'est pas
configuré → le canal reste utilisable (modération via page admin), sans planter au démarrage.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

_CID_PHOTO = "vignette"


@dataclass
class Mail:
    """Message prêt à partir. `images_inline` : {content_id: octets JPEG} (référencés `cid:`)."""

    destinataire: str
    sujet: str
    html: str
    texte: str
    images_inline: dict[str, bytes] = field(default_factory=dict)


class Mailer(Protocol):
    def envoyer(self, mail: Mail) -> None: ...


def _lien_action(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/api/contrib/action/{token}"


def mail_moderation(
    *,
    destinataire: str,
    base_url: str,
    tokens: dict[str, str],
    vignette: bytes,
    feu_public_id: str | None,
    captured_at: str,
    distance_km: float | None,
    score_nsfw: float | None,
    score_feu: float | None,
) -> Mail:
    """Construit le mail de modération : vignette inline + détails + 3 liens signés.

    `tokens` = `{"publier":…, "rejeter":…, "blacklister":…}`. Les liens sont en **GET** (le
    endpoint n'agit qu'au POST de confirmation, §6) — un préchargement par le client mail
    n'a donc aucun effet.
    """
    liens = {a: _lien_action(base_url, t) for a, t in tokens.items()}
    feu = feu_public_id or "(non rattaché)"
    dist = f"{distance_km:.1f} km" if distance_km is not None else "?"
    texte = (
        f"Nouvelle contribution à modérer.\n"
        f"Feu : {feu}\nPrise de vue : {captured_at}\nDistance : {dist}\n"
        f"Scores auto — nsfw={score_nsfw} feu={score_feu}\n\n"
        f"Publier : {liens['publier']}\nRejeter : {liens['rejeter']}\n"
        f"Blacklister l'IP : {liens['blacklister']}\n"
    )
    html = (
        f"<h2>Contribution à modérer</h2>"
        f'<p><img src="cid:{_CID_PHOTO}" alt="vignette" style="max-width:480px"></p>'
        f"<ul><li>Feu : <b>{feu}</b></li><li>Prise de vue : {captured_at}</li>"
        f"<li>Distance : {dist}</li>"
        f"<li>Scores auto — nsfw={score_nsfw}, feu={score_feu}</li></ul>"
        f'<p><a href="{liens["publier"]}">Publier</a> · '
        f'<a href="{liens["rejeter"]}">Rejeter</a> · '
        f'<a href="{liens["blacklister"]}">Blacklister l\'IP</a></p>'
        f"<p style=\"color:#888;font-size:12px\">Ces liens ouvrent une page de confirmation ; "
        f"rien n'est appliqué avant validation.</p>"
    )
    return Mail(destinataire, "Sentifeu — contribution à modérer", html, texte,
                {_CID_PHOTO: vignette})


def mail_publication(*, destinataire: str, base_url: str, feu_public_id: str | None) -> Mail:
    """Notification au contributeur : sa photo est publiée (§6)."""
    lien = base_url.rstrip("/") + (f"/feux/{feu_public_id}" if feu_public_id else "")
    texte = (
        "Bonjour,\n\nVotre photo a été publiée sur Sentifeu. Merci de votre contribution.\n"
        + (f"\n{lien}\n" if feu_public_id else "")
    )
    html = (
        "<p>Bonjour,</p><p>Votre photo a été <b>publiée</b> sur Sentifeu. "
        "Merci de votre contribution.</p>"
        + (f'<p><a href="{lien}">Voir le feu</a></p>' if feu_public_id else "")
    )
    return Mail(destinataire, "Sentifeu — votre photo est publiée", html, texte)


class MailerSMTP:
    """Adaptateur SMTP mince (STARTTLS). Construit depuis l'environnement par `mailer_depuis_env`."""

    def __init__(self, *, host: str, port: int, user: str | None, password: str | None,
                 expediteur: str):
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._expediteur = expediteur

    def _construire(self, mail: Mail) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self._expediteur
        msg["To"] = mail.destinataire
        msg["Subject"] = mail.sujet
        msg.set_content(mail.texte)
        msg.add_alternative(mail.html, subtype="html")
        # Les images inline se rattachent à la partie HTML (dernière alternative).
        partie_html = msg.get_payload()[-1]
        for cid, octets in mail.images_inline.items():
            partie_html.add_related(octets, maintype="image", subtype="jpeg", cid=f"<{cid}>")
        return msg

    def _auth(self, s) -> None:
        if self._user:
            s.login(self._user, self._password or "")

    def envoyer(self, mail: Mail) -> None:
        """Envoie via SSL implicite (port 465) ou STARTTLS (587, défaut) selon le port.

        Beaucoup d'hébergeurs mutualisés (o2switch, OVH…) n'exposent que le 465 : la détection
        évite un échec silencieux si `CONTRIB_SMTP_PORT=465`.
        """
        msg = self._construire(mail)
        ctx = ssl.create_default_context()
        if self._port == 465:
            with smtplib.SMTP_SSL(self._host, self._port, timeout=30, context=ctx) as s:
                self._auth(s)
                s.send_message(msg)
        else:
            with smtplib.SMTP(self._host, self._port, timeout=30) as s:
                s.starttls(context=ctx)
                self._auth(s)
                s.send_message(msg)


def mailer_depuis_env(config: dict) -> Mailer | None:
    """Construit un `MailerSMTP` depuis `CONTRIB_SMTP_*`, ou `None` si le host n'est pas configuré."""
    host = os.environ.get("CONTRIB_SMTP_HOST")
    if not host:
        return None
    return MailerSMTP(
        host=host,
        port=int(os.environ.get("CONTRIB_SMTP_PORT", "587")),
        user=os.environ.get("CONTRIB_SMTP_USER"),
        password=os.environ.get("CONTRIB_SMTP_PASSWORD"),
        expediteur=config["contributions"]["mail_expediteur"],
    )
