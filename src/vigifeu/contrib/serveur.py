"""Runner WSGI du service contributif (Spec 10 §2) — `python -m vigifeu.contrib.serveur`.

Service **same-origin** monté sous `/api/contrib` (+ `/admin/contrib`) par le reverse proxy
(Nginx), servi par **waitress** (WSGI stdlib-friendly, peu de threads : contrainte VPS 2 vCPU).
Distinct du daemon d'ingestion : la base contributions est écrite ici, la socle lue en
read-only — l'invariant « un seul écrivain sur la socle = le daemon » reste vrai (plan §1.1).

Config par l'environnement (comme le daemon) :
- `VIGIFEU_CONFIG` (défaut `config/params.toml`) ;
- `CONTRIB_HOST` (défaut `127.0.0.1` — jamais exposé en direct, Nginx devant) ;
- `CONTRIB_PORT` (défaut `8081`) ;
- `CONTRIB_THREADS` (défaut `4`).

Secrets d'env lus par `create_app` : `CONTRIB_HASH_SECRET`, `CONTRIB_SMTP_*`,
`CONTRIB_MODERATION_EMAIL`, `CONTRIB_ADMIN_USER/PASSWORD`.
"""

from __future__ import annotations

import logging
import os
import sys

from waitress import serve

from vigifeu.contrib.app import create_app
from vigifeu.contrib.jobs import construire_planificateur

log = logging.getLogger("vigifeu.contrib")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,  # journald récupère stdout sous systemd
    )
    app = create_app()  # migre la base contributions au démarrage (create_app)
    config = app.config["VIGIFEU"]
    host = os.environ.get("CONTRIB_HOST", "127.0.0.1")
    port = int(os.environ.get("CONTRIB_PORT", "8081"))
    threads = int(os.environ.get("CONTRIB_THREADS", "4"))

    # Jobs de fond (auto-filtre + purge) dans le même process, en background : waitress bloque
    # ensuite le thread principal. Arrêt propre en sortie.
    planificateur = construire_planificateur(config)
    planificateur.start()
    log.info("service contributif à l'écoute sur %s:%s (%s threads)", host, port, threads)
    try:
        serve(app, host=host, port=port, threads=threads)
    finally:
        planificateur.shutdown(wait=False)


if __name__ == "__main__":
    main()
