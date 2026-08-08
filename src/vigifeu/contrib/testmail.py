"""Commande de test SMTP (Spec 10 §6) — `python -m vigifeu.contrib.testmail [destinataire]`.

Envoie un mail de test avec les réglages `CONTRIB_SMTP_*` de l'environnement (détection
465/587 automatique). Sert à valider la config e-mail avant d'activer le canal, sans passer
par un vrai dépôt. Affiche un message clair OK / ÉCHEC (avec l'erreur SMTP).

Sur le VPS, les secrets vivent dans le .env du service (pas dans le shell) — charge-le d'abord :
    cd /opt/vigifeu
    sudo bash -c 'set -a; . ./.env; set +a; .venv/bin/python -m vigifeu.contrib.testmail contact@sentifeu.fr'
"""

from __future__ import annotations

import os
import sys

from vigifeu.contrib.mail import Mail, mailer_depuis_env
from vigifeu.model.db import load_config


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    config = load_config(os.environ.get("VIGIFEU_CONFIG", "config/params.toml"))

    destinataire = argv[0] if argv else os.environ.get("CONTRIB_MODERATION_EMAIL")
    if not destinataire:
        print("ÉCHEC : destinataire requis (argument ou CONTRIB_MODERATION_EMAIL).")
        return 2

    mailer = mailer_depuis_env(config)
    if mailer is None:
        print("ÉCHEC : CONTRIB_SMTP_HOST absent de l'environnement (SMTP non configuré).")
        return 2

    hote = os.environ.get("CONTRIB_SMTP_HOST")
    port = os.environ.get("CONTRIB_SMTP_PORT", "587")
    print(f"Envoi d'un test via {hote}:{port} → {destinataire} …")
    try:
        mailer.envoyer(Mail(
            destinataire,
            "Sentifeu — test SMTP",
            "<p>Test d'envoi Sentifeu réussi ✅</p>",
            "Test d'envoi Sentifeu réussi.",
        ))
    except Exception as exc:  # noqa: BLE001 - on veut afficher toute erreur SMTP à l'opérateur
        print(f"ÉCHEC — {type(exc).__name__}: {exc}")
        return 1
    print(f"OK — mail de test envoyé à {destinataire}. Vérifie la boîte de réception (et les spams).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
