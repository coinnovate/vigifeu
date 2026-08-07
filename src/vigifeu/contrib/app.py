"""Mini-API Flask du canal contributif (Spec 10 §2).

Service **same-origin** monté sous `/api/contrib` par le reverse proxy existant. Ce module
fournit la factory `create_app` et les endpoints publics du parcours de dépôt : **santé**,
**feux-proches** (§4.2) et **dépôt** (§4.6). Les endpoints d'exposition (widget, service
d'images) et de modération viendront aux étapes suivantes du §12.

Déploiement : service systemd distinct servi par un WSGI (waitress/gunicorn), peu de workers.
La base contributions est **écrite** par l'API ; la socle est lue en **read-only** (contrib/db.py),
ce qui préserve l'invariant « un seul écrivain sur la socle = le daemon » (plan §1.1).
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta

from flask import Blueprint, Flask, current_app, jsonify, request

from vigifeu.contrib.db import connect_contrib, connect_socle_readonly, migrate_contrib
from vigifeu.contrib.images import ImageInvalide, ecrire_paire, encoder_image
from vigifeu.contrib.ip import client_ip, hash_ip, ip_bloquee, quota_atteint
from vigifeu.contrib.socle import commune_du_point, feux_proches, valider_ancre
from vigifeu.model.db import current_version, load_config

bp = Blueprint("contrib", __name__, url_prefix="/api/contrib")

# Validation e-mail volontairement permissive (facultatif, NON vérifié — seul usage : notifier
# de la publication ; §4). On écarte seulement les formes manifestement invalides.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CONSENT_VRAI = {"1", "true", "on", "oui", "yes"}


def _now_iso() -> str:
    """Horodatage serveur ISO UTC (même format que la socle : `...Z`)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _territoire(config: dict) -> tuple[float, float, float, float]:
    """Bornes (lon_min, lat_min, lon_max, lat_max) du territoire, depuis `general.firms_bbox`."""
    w, s, e, n = (float(v) for v in config["general"]["firms_bbox"].split(","))
    return w, s, e, n


def _hors_territoire(config: dict, lat: float, lon: float) -> bool:
    """True si le point sort de la zone couverte (rejet des coordonnées aberrantes, §4)."""
    w, s, e, n = _territoire(config)
    return not (s <= lat <= n and w <= lon <= e)


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


@bp.get("/feux-proches")
def feux_proches_endpoint():
    """Feux publiés à moins de `rayon_max_km` de (lat, lon), triés par distance (§4).

    `lat`/`lon` requis, décimaux, **bornés au territoire** (aucune donnée perso en query :
    c'est la géoloc live, pas l'auteur). Socle absente → liste vide (dégradation, jamais 500).
    """
    config = current_app.config["VIGIFEU"]
    contrib_cfg = config["contributions"]

    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
    except (KeyError, ValueError):
        return jsonify({"error": "lat et lon requis (nombres décimaux)"}), 400
    if _hors_territoire(config, lat, lon):
        return jsonify({"error": "coordonnées hors zone couverte"}), 400

    try:
        sc = connect_socle_readonly(config["general"]["db_path"])
    except FileNotFoundError:
        return jsonify({"feux": []})  # socle non déployée → aucun feu, pas d'erreur
    try:
        feux = feux_proches(sc, lat, lon, contrib_cfg["rayon_max_km"])
    finally:
        sc.close()
    return jsonify({"feux": feux})


@bp.post("/deposer")
def deposer():
    """Dépôt d'une contribution photo (§4.6) : encode, ancre, commune, quota IP → `soumise`.

    Multipart : `image` (blob canvas) + `fire_event_id` + `hotspot_raw_id` + `lat`/`lon`
    (géoloc live) + `consent` (obligatoire) + `email?`. Contrôles en cascade :
    consentement → coordonnées bornées → blocklist/quota IP → ancre valide (feu publié,
    hotspot < rayon) → image décodable → dédup `sha256`. La position de l'auteur n'est
    jamais stockée (seule `distance_km`). Réponses : 201 `soumise`, 200 `doublon`, 4xx.
    """
    config = current_app.config["VIGIFEU"]
    contrib_cfg = config["contributions"]

    secret = current_app.config.get("CONTRIB_HASH_SECRET")
    if not secret:
        return jsonify({"error": "canal indisponible (secret manquant)"}), 503

    # 1. Consentement obligatoire (RGPD/LCEN, §11) — avant tout traitement.
    if (request.form.get("consent") or "").strip().lower() not in _CONSENT_VRAI:
        return jsonify({"error": "consentement requis"}), 400

    # 2. Feu choisi + géoloc live.
    try:
        fire_event_id = int(request.form["fire_event_id"])
        hotspot_raw_id = int(request.form["hotspot_raw_id"])
        lat = float(request.form["lat"])
        lon = float(request.form["lon"])
    except (KeyError, ValueError):
        return jsonify({"error": "feu et géoloc requis (identifiants + lat/lon décimaux)"}), 400
    if _hors_territoire(config, lat, lon):
        return jsonify({"error": "coordonnées hors zone couverte"}), 400

    # 3. E-mail facultatif, validé de forme s'il est fourni.
    email = (request.form.get("email") or "").strip() or None
    if email and not _EMAIL_RE.match(email):
        return jsonify({"error": "e-mail invalide"}), 400

    # 4. Image présente (la taille est plafonnée en amont par MAX_CONTENT_LENGTH → 413).
    fichier = request.files.get("image")
    if fichier is None:
        return jsonify({"error": "image requise"}), 400
    raw = fichier.read()
    if not raw:
        return jsonify({"error": "image vide"}), 400

    # 5. Anti-abus IP (blocklist + quota 24 h) sur la base contributions.
    ip = client_ip(request.remote_addr, request.headers.get("X-Forwarded-For"))
    ip_h = hash_ip(ip, secret)
    now = _now_iso()
    depuis = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    cc = connect_contrib(contrib_cfg["db_path"])
    try:
        if ip_bloquee(cc, ip_h, now_iso=now):
            return jsonify({"error": "dépôt refusé"}), 403
        if quota_atteint(cc, ip_h, max_jour=contrib_cfg["max_photos_ip_jour"], depuis_iso=depuis):
            return jsonify({"error": "quota de dépôts atteint, réessayez plus tard"}), 429

        # 6. Ancre : feu publié + hotspot < rayon (socle lecture seule). Commune du hotspot.
        try:
            sc = connect_socle_readonly(config["general"]["db_path"])
        except FileNotFoundError:
            return jsonify({"error": "socle indisponible"}), 503
        try:
            ancre = valider_ancre(
                sc, lat, lon, fire_event_id, hotspot_raw_id, contrib_cfg["rayon_max_km"]
            )
            if ancre is None:
                return jsonify({"error": "feu non valide ou hors rayon"}), 422
            code_insee = commune_du_point(sc, ancre["hs_lat"], ancre["hs_lon"])
        finally:
            sc.close()

        # 7. Encodage 2 tailles (sans EXIF, sha256).
        try:
            enc = encoder_image(
                raw,
                max_px=contrib_cfg["max_px"],
                thumb_px=contrib_cfg["thumb_px"],
                qualite=contrib_cfg["jpeg_qualite"],
            )
        except ImageInvalide:
            return jsonify({"error": "image illisible"}), 400

        # 8. Dédup : même image déjà connue → no-op idempotent (anti-re-spam voulu, §3.4).
        existant = cc.execute(
            "SELECT id FROM contribution WHERE image_sha256 = ?", (enc.image_sha256,)
        ).fetchone()
        if existant is not None:
            return jsonify({"statut": "doublon", "id": existant["id"]}), 200

        # 9. Écriture des images HORS répertoire public, puis insertion en `soumise`.
        image_path, thumb_path = ecrire_paire(enc, contrib_cfg["store_dir"], enc.image_sha256)
        try:
            cur = cc.execute(
                "INSERT INTO contribution ("
                " fire_event_id, hotspot_raw_id, distance_km, captured_at,"
                " image_path, thumb_path, image_sha256, largeur, hauteur,"
                " thumb_largeur, thumb_hauteur, email, ip_hash, consentement_at,"
                " cgu_version, code_insee, statut, created_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'soumise', ?)",
                (
                    fire_event_id, hotspot_raw_id, ancre["distance_km"], now,
                    image_path, thumb_path, enc.image_sha256, enc.largeur, enc.hauteur,
                    enc.thumb_largeur, enc.thumb_hauteur, email, ip_h, now,
                    contrib_cfg["cgu_version_courante"], code_insee, now,
                ),
            )
            cc.commit()
        except sqlite3.IntegrityError:
            # Course : même sha256 inséré entre-temps → doublon (fichiers identiques, inoffensifs).
            existant = cc.execute(
                "SELECT id FROM contribution WHERE image_sha256 = ?", (enc.image_sha256,)
            ).fetchone()
            return jsonify({"statut": "doublon", "id": existant["id"] if existant else None}), 200

        return jsonify({"statut": "soumise", "id": cur.lastrowid, "code_insee": code_insee}), 201
    finally:
        cc.close()


def create_app(config: dict | None = None) -> Flask:
    """Construit l'app Flask et migre la base contributions au démarrage.

    `config` peut être injectée (tests) ; sinon chargée depuis `VIGIFEU_CONFIG` /
    `config/params.toml` (même convention que la CLI, cf. `cli._open`).
    """
    app = Flask(__name__)
    if config is None:
        config = load_config(os.environ.get("VIGIFEU_CONFIG", "config/params.toml"))
    app.config["VIGIFEU"] = config

    # Plafond d'upload appliqué par Werkzeug AVANT lecture mémoire (§4) → 413 si dépassé.
    app.config["MAX_CONTENT_LENGTH"] = int(config["contributions"]["max_upload_mo"]) * 1024 * 1024
    # Sel du hachage IP + tokens (secret d'env, jamais dans le dépôt). Absent → /deposer répond 503.
    app.config["CONTRIB_HASH_SECRET"] = os.environ.get("CONTRIB_HASH_SECRET")

    @app.errorhandler(413)
    def _trop_gros(_e):
        return jsonify({"error": "image trop volumineuse"}), 413

    cc = connect_contrib(config["contributions"]["db_path"])
    try:
        migrate_contrib(cc)
    finally:
        cc.close()

    app.register_blueprint(bp)
    return app
