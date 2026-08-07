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

import hmac
import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from html import escape

from flask import Blueprint, Flask, Response, current_app, jsonify, request

from vigifeu.contrib.db import connect_contrib, connect_socle_readonly, migrate_contrib
from vigifeu.contrib.images import ImageInvalide, ecrire_paire, encoder_image
from vigifeu.contrib.ip import client_ip, hash_ip, ip_bloquee, quota_atteint
from vigifeu.contrib.mail import mail_publication
from vigifeu.contrib.moderation import blacklister, publier, rejeter, signaler
from vigifeu.contrib.socle import commune_du_point, feux_proches, valider_ancre
from vigifeu.contrib.tokens import verifier_token
from vigifeu.model.db import current_version, load_config

bp = Blueprint("contrib", __name__, url_prefix="/api/contrib")
admin_bp = Blueprint("contrib_admin", __name__, url_prefix="/admin/contrib")

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


def _page(titre: str, corps: str, code: int = 200) -> tuple[str, int]:
    """Petite page HTML autoportante (pas de dépendance gabarit ; contenu déjà échappé)."""
    html = (
        f"<!doctype html><meta charset=utf-8><title>{escape(titre)}</title>"
        f"<body style='font-family:system-ui;max-width:640px;margin:3rem auto'>"
        f"<h1>{escape(titre)}</h1>{corps}</body>"
    )
    return html, code


def _feu_public_id(config: dict, cc: sqlite3.Connection, cid: int) -> str | None:
    """public_id du feu socle rattaché à une contribution (best-effort, pour le lien de notif)."""
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


def _appliquer_action(cc: sqlite3.Connection, config: dict, action: str, cid: int, *, par: str) -> dict:
    """Applique une décision de modération (§6). Toutes idempotentes (statut='a_moderer')."""
    cfg = config["contributions"]
    if action == "publier":
        return publier(cc, cid, par=par)
    if action == "rejeter":
        return rejeter(cc, cid, par=par, motif="rejet modération", purge_mois=cfg["purge_rejetees_mois"])
    if action == "blacklister":
        return blacklister(
            cc, cid, par=par, expire_jours=cfg["ip_block_expire_jours"],
            purge_mois=cfg["purge_rejetees_mois"],
        )
    return {"applique": False}


def _notifier_publication(config: dict, cc: sqlite3.Connection, out: dict, cid: int) -> None:
    """Notifie le contributeur (si email + mailer configuré). N'échoue jamais la requête."""
    if not out.get("applique") or not out.get("email"):
        return
    mailer = current_app.config.get("CONTRIB_MAILER")
    if mailer is None:
        return
    try:
        mailer.envoyer(mail_publication(
            destinataire=out["email"],
            base_url=config["generate"]["base_url"],
            feu_public_id=_feu_public_id(config, cc, cid),
        ))
    except Exception:  # pragma: no cover - dépend du relais SMTP
        current_app.logger.warning("notification de publication non envoyée", exc_info=True)


@bp.get("/action/<token>")
def action_confirmer(token):
    """Page de CONFIRMATION (aucun effet) — garde-fou anti-préchargement (§6).

    Les clients mail préchargent les liens : un GET ne doit RIEN muter. On se contente
    d'afficher un bouton qui POST le même token.
    """
    secret = current_app.config.get("CONTRIB_HASH_SECRET")
    if not secret:
        return _page("Indisponible", "<p>Canal non configuré.</p>", 503)
    data = verifier_token(token, secret=secret)
    if data is None:
        return _page("Lien invalide", "<p>Ce lien est invalide ou a expiré.</p>", 410)

    libelle = {"publier": "Publier", "rejeter": "Rejeter", "blacklister": "Blacklister l'IP"}[data["action"]]
    corps = (
        f"<p>Action : <b>{escape(libelle)}</b> (contribution #{data['cid']}).</p>"
        f"<form method=post action='/api/contrib/action/{escape(token)}'>"
        f"<button type=submit>Confirmer : {escape(libelle)}</button></form>"
    )
    return _page("Confirmer l'action", corps)


@bp.post("/action/<token>")
def action_appliquer(token):
    """Applique l'action APRÈS confirmation (POST). Seul chemin qui mute l'état (§6)."""
    secret = current_app.config.get("CONTRIB_HASH_SECRET")
    if not secret:
        return _page("Indisponible", "<p>Canal non configuré.</p>", 503)
    data = verifier_token(token, secret=secret)
    if data is None:
        return _page("Lien invalide", "<p>Ce lien est invalide ou a expiré.</p>", 410)

    config = current_app.config["VIGIFEU"]
    cc = connect_contrib(config["contributions"]["db_path"])
    try:
        out = _appliquer_action(cc, config, data["action"], data["cid"], par="mail")
        if data["action"] == "publier":
            _notifier_publication(config, cc, out, data["cid"])
    finally:
        cc.close()

    msg = "Action appliquée." if out.get("applique") else "Déjà traité — aucun changement."
    return _page("Modération", f"<p>{escape(msg)}</p>")


@bp.post("/signaler")
def signaler_endpoint():
    """Signalement public (LCEN, §6) : délistage immédiat + re-modération. Réponse neutre."""
    public_id = (request.form.get("public_id") or "").strip()
    if not public_id:
        return jsonify({"error": "public_id requis"}), 400
    config = current_app.config["VIGIFEU"]
    cc = connect_contrib(config["contributions"]["db_path"])
    try:
        signaler(cc, public_id)  # no-op si inconnu/non publié — réponse identique (pas d'énumération)
    finally:
        cc.close()
    return jsonify({"status": "reçu"})


# --- Modération : page admin (auth) --------------------------------------

def _admin_etat() -> bool | None:
    """True si authentifié, False si mauvais identifiants, None si admin non configuré (503)."""
    user = current_app.config.get("CONTRIB_ADMIN_USER")
    pwd = current_app.config.get("CONTRIB_ADMIN_PASSWORD")
    if not user or not pwd:
        return None
    auth = request.authorization
    if auth and hmac.compare_digest(auth.username or "", user) and hmac.compare_digest(
        auth.password or "", pwd
    ):
        return True
    return False


def _exige_admin():
    """Retourne une réponse d'erreur si l'accès admin est refusé, sinon None."""
    etat = _admin_etat()
    if etat is None:
        return _page("Indisponible", "<p>Administration non configurée.</p>", 503)
    if not etat:
        return Response(
            "Authentification requise", 401,
            {"WWW-Authenticate": 'Basic realm="contrib"'},
        )
    return None


@admin_bp.get("")
def admin_file():
    """File de modération `a_moderer` : vignette (route auth) + détails + boutons d'action."""
    refus = _exige_admin()
    if refus is not None:
        return refus
    config = current_app.config["VIGIFEU"]
    cc = connect_contrib(config["contributions"]["db_path"])
    try:
        rows = cc.execute(
            "SELECT id, fire_event_id, captured_at, distance_km, score_nsfw, score_feu, code_insee "
            "FROM contribution WHERE statut='a_moderer' ORDER BY captured_at DESC"
        ).fetchall()
    finally:
        cc.close()

    if not rows:
        return _page("Modération", "<p>File vide.</p>")
    cartes = []
    for r in rows:
        boutons = "".join(
            f"<form method=post action='/admin/contrib/action' style='display:inline'>"
            f"<input type=hidden name=cid value={r['id']}>"
            f"<input type=hidden name=action value={a}>"
            f"<button type=submit>{escape(lib)}</button></form> "
            for a, lib in (("publier", "Publier"), ("rejeter", "Rejeter"),
                           ("blacklister", "Blacklister l'IP"))
        )
        cartes.append(
            f"<div style='border:1px solid #ccc;padding:1rem;margin:1rem 0'>"
            f"<img src='/admin/contrib/photo/{r['id']}' alt='vignette' style='max-width:320px'><br>"
            f"<small>#{r['id']} · feu {r['fire_event_id']} · {escape(r['captured_at'])} · "
            f"{r['distance_km']} km · nsfw={r['score_nsfw']} feu={r['score_feu']}</small><br>"
            f"{boutons}</div>"
        )
    return _page("Modération", "".join(cartes))


@admin_bp.post("/action")
def admin_action():
    """Applique une action depuis la page admin (`moderee_par='admin'`)."""
    refus = _exige_admin()
    if refus is not None:
        return refus
    try:
        cid = int(request.form["cid"])
        action = request.form["action"]
    except (KeyError, ValueError):
        return _page("Erreur", "<p>Requête invalide.</p>", 400)

    config = current_app.config["VIGIFEU"]
    cc = connect_contrib(config["contributions"]["db_path"])
    try:
        out = _appliquer_action(cc, config, action, cid, par="admin")
        if action == "publier":
            _notifier_publication(config, cc, out, cid)
    finally:
        cc.close()
    msg = "Action appliquée." if out.get("applique") else "Déjà traité — aucun changement."
    return _page("Modération", f"<p>{escape(msg)}</p> <p><a href='/admin/contrib'>Retour</a></p>")


@admin_bp.get("/photo/<int:cid>")
def admin_photo(cid):
    """Sert la vignette d'une contribution NON publiée — **uniquement authentifié** (§6)."""
    refus = _exige_admin()
    if refus is not None:
        return refus
    config = current_app.config["VIGIFEU"]
    cc = connect_contrib(config["contributions"]["db_path"])
    try:
        r = cc.execute("SELECT thumb_path FROM contribution WHERE id=?", (cid,)).fetchone()
    finally:
        cc.close()
    if r is None or not r["thumb_path"] or not os.path.exists(r["thumb_path"]):
        return Response("introuvable", 404)
    with open(r["thumb_path"], "rb") as f:
        octets = f.read()
    return Response(octets, mimetype="image/jpeg", headers={"Cache-Control": "private, no-store"})


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
    # Identifiants de la page admin (basic auth) et relais mail — tous depuis l'environnement.
    app.config["CONTRIB_ADMIN_USER"] = os.environ.get("CONTRIB_ADMIN_USER")
    app.config["CONTRIB_ADMIN_PASSWORD"] = os.environ.get("CONTRIB_ADMIN_PASSWORD")
    from vigifeu.contrib.mail import mailer_depuis_env

    app.config["CONTRIB_MAILER"] = mailer_depuis_env(config)

    @app.errorhandler(413)
    def _trop_gros(_e):
        return jsonify({"error": "image trop volumineuse"}), 413

    cc = connect_contrib(config["contributions"]["db_path"])
    try:
        migrate_contrib(cc)
    finally:
        cc.close()

    app.register_blueprint(bp)
    app.register_blueprint(admin_bp)
    return app
