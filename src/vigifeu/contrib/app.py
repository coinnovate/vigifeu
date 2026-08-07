"""Mini-API Flask du canal contributif (Spec 10 §2).

Service **same-origin** monté sous `/api/contrib` par le reverse proxy existant. Ce module
fournit la factory `create_app` et, à ce stade (étape 1), l'endpoint de **santé**. Les
endpoints métier (feux-proches, dépôt, service d'images, widget, modération…) viendront aux
étapes suivantes du §12.

Déploiement : service systemd distinct servi par un WSGI (waitress/gunicorn), peu de workers.
La base contributions est **écrite** par l'API ; la socle est lue en **read-only** (contrib/db.py),
ce qui préserve l'invariant « un seul écrivain sur la socle = le daemon » (plan §1.1).
"""

from __future__ import annotations

import os

from flask import Blueprint, Flask, current_app, jsonify

from vigifeu.contrib.db import connect_contrib, connect_socle_readonly, migrate_contrib
from vigifeu.model.db import current_version, load_config

bp = Blueprint("contrib", __name__, url_prefix="/api/contrib")


@bp.get("/health")
def health():
    """Sonde : process up, base contributions migrée, socle joignable en lecture (info)."""
    config = current_app.config["VIGIFEU"]
    contrib_cfg = config["contributions"]

    cc = connect_contrib(contrib_cfg["db_path"])
    try:
        schema = current_version(cc)
    finally:
        cc.close()

    socle_reachable = True
    try:
        sc = connect_socle_readonly(config["general"]["db_path"])
        sc.execute("SELECT 1").fetchone()
        sc.close()
    except Exception:
        socle_reachable = False

    return jsonify(
        {
            "status": "ok",
            "activated": bool(contrib_cfg.get("activated", False)),
            "schema_version": schema,
            "socle_reachable": socle_reachable,
        }
    )


def create_app(config: dict | None = None) -> Flask:
    """Construit l'app Flask et migre la base contributions au démarrage.

    `config` peut être injectée (tests) ; sinon chargée depuis `VIGIFEU_CONFIG` /
    `config/params.toml` (même convention que la CLI, cf. `cli._open`).
    """
    app = Flask(__name__)
    if config is None:
        config = load_config(os.environ.get("VIGIFEU_CONFIG", "config/params.toml"))
    app.config["VIGIFEU"] = config

    cc = connect_contrib(config["contributions"]["db_path"])
    try:
        migrate_contrib(cc)
    finally:
        cc.close()

    app.register_blueprint(bp)
    return app
