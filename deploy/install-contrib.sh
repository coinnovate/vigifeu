#!/usr/bin/env bash
# deploy/install-contrib.sh — installe le service contributif Sentifeu (Spec 10) sur le VPS.
#
# À lancer UNE FOIS, en root, après avoir déployé le code (git pull) :
#   sudo /opt/vigifeu/deploy/install-contrib.sh
#
# Enchaîne :
#   1. vérifie les secrets requis dans .env (CONTRIB_HASH_SECRET obligatoire) ;
#   2. uv sync (récupère waitress + Pillow, dépendances de prod) ;
#   3. installe + active l'unité systemd vigifeu-contrib ;
#   4. vérifie que le service répond (/api/contrib/health) ;
#   5. rappelle les étapes manuelles restantes (include Nginx, DNS SMTP, activation).
#
# Les mises à jour ultérieures passent par update.sh (qui redémarre aussi ce service).
# Variables surchargeables : APP_DIR, APP_USER, SERVICE, CONTRIB_PORT.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/vigifeu}"
APP_USER="${APP_USER:-vigifeu}"
SERVICE="${SERVICE:-vigifeu-contrib}"
ENV_FILE="$APP_DIR/.env"
PORT="${CONTRIB_PORT:-8081}"

# --- garde-fous -------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
  echo "Ce script doit être lancé en root (systemctl) : sudo $0" >&2
  exit 1
fi
if [ ! -d "$APP_DIR" ]; then
  echo "APP_DIR introuvable : $APP_DIR (déployer le code d'abord)." >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "uv introuvable. Installer :" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh" >&2
  exit 1
fi

run_as_app() { sudo -u "$APP_USER" "$@"; }

# --- 1. secrets d'environnement --------------------------------------------
echo "==> vérification des secrets ($ENV_FILE)"
if [ ! -f "$ENV_FILE" ]; then
  echo "ÉCHEC : $ENV_FILE absent. Créez-le (voir vigifeu-contrib.service)." >&2
  exit 1
fi
if ! grep -q '^CONTRIB_HASH_SECRET=' "$ENV_FILE"; then
  echo "ÉCHEC : CONTRIB_HASH_SECRET manquant dans $ENV_FILE (obligatoire : hachage IP + tokens)." >&2
  echo "  Générer :  echo \"CONTRIB_HASH_SECRET=\$(openssl rand -hex 32)\" >> $ENV_FILE" >&2
  exit 1
fi
for opt in CONTRIB_ADMIN_USER CONTRIB_ADMIN_PASSWORD CONTRIB_SMTP_HOST CONTRIB_MODERATION_EMAIL; do
  grep -q "^$opt=" "$ENV_FILE" || echo "   (info) $opt absent — fonctionnalité liée désactivée."
done

# --- 2. dépendances ---------------------------------------------------------
echo "==> uv sync (dépendances de production)"
run_as_app env UV_CACHE_DIR="${UV_CACHE_DIR:-$APP_DIR/.uv-cache}" uv sync

# --- 3. unité systemd -------------------------------------------------------
echo "==> installation de l'unité systemd $SERVICE"
cp "$APP_DIR/deploy/vigifeu-contrib.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable --now "$SERVICE"
sleep 2

# --- 4. vérification --------------------------------------------------------
if ! systemctl is-active --quiet "$SERVICE"; then
  echo "ÉCHEC : $SERVICE n'est pas actif." >&2
  echo "Diagnostic : journalctl -u $SERVICE -n 50 --no-pager" >&2
  exit 1
fi
echo "==> sonde de santé (http://127.0.0.1:$PORT/api/contrib/health)"
if command -v curl >/dev/null 2>&1; then
  curl -fsS "http://127.0.0.1:$PORT/api/contrib/health" && echo || {
    echo "ÉCHEC : /api/contrib/health ne répond pas." >&2
    echo "Diagnostic : journalctl -u $SERVICE -n 50 --no-pager" >&2
    exit 1
  }
fi

# --- 5. étapes manuelles restantes -----------------------------------------
cat <<EOF

✅ Service $SERVICE actif (port $PORT, en local — Nginx devant).

Étapes restantes (manuelles) :
  1. Nginx — inclure le same-origin dans le server{} HTTPS de sentifeu.fr :
       include $APP_DIR/deploy/nginx-contrib.conf.snippet;
     puis :  nginx -t && systemctl reload nginx
  2. SMTP (optionnel) — renseigner CONTRIB_SMTP_* + CONTRIB_MODERATION_EMAIL dans $ENV_FILE,
     vérifier le domaine chez le fournisseur (SPF/DKIM/DMARC), puis : systemctl restart $SERVICE
  3. Test bout-en-bout — mettre [contributions].mode_demo = true (config/params.toml),
     restart, tester depuis un mobile via HTTPS, puis remettre à false.
  4. Ouverture — quand CGU/mentions sont validées : [contributions].activated = true, restart.

Logs : journalctl -u $SERVICE -f
EOF
