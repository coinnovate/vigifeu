#!/usr/bin/env bash
# deploy/update.sh — met à jour Vigifeu en production en une commande.
#
# À lancer sur le VPS, en root (le restart systemd l'exige) :
#   sudo /opt/vigifeu/deploy/update.sh
#
# Enchaîne, dans l'ordre :
#   1. git pull            (en tant que l'utilisateur du service, pour l'ownership)
#   2. uv sync             (dépendances de prod, depuis uv.lock)
#   3. systemctl restart   (le daemon applique lui-même la migration de schéma au boot)
#   4. vérification        (service actif + lignes de démarrage + version de schéma)
#
# Variables surchargeables : APP_DIR, APP_USER, SERVICE, UV_CACHE_DIR.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/vigifeu}"
APP_USER="${APP_USER:-vigifeu}"
SERVICE="${SERVICE:-vigifeu}"
UV_CACHE="${UV_CACHE_DIR:-$APP_DIR/.uv-cache}"

# --- garde-fous -------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
  echo "Ce script doit être lancé en root (systemctl restart) : sudo $0" >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "uv introuvable. Installer :" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh" >&2
  exit 1
fi

run_as_app() { sudo -u "$APP_USER" "$@"; }

cd "$APP_DIR"

echo "==> git pull ($APP_DIR, utilisateur $APP_USER)"
run_as_app git -C "$APP_DIR" pull --ff-only

echo "==> uv sync (dépendances de production)"
run_as_app env UV_CACHE_DIR="$UV_CACHE" uv sync

echo "==> redémarrage du service $SERVICE"
SINCE="$(date '+%Y-%m-%d %H:%M:%S')"
systemctl restart "$SERVICE"
sleep 3

# --- vérification -----------------------------------------------------------
if ! systemctl is-active --quiet "$SERVICE"; then
  echo "ÉCHEC : le service n'est pas actif après redémarrage." >&2
  echo "Diagnostic : journalctl -u $SERVICE -n 50 --no-pager" >&2
  exit 1
fi

# Version de schéma courante (lecture seule, sûre en WAL même daemon actif).
VERSION="$(run_as_app "$APP_DIR/.venv/bin/python" - <<'PY' || echo '?'
import sqlite3, tomllib
cfg = tomllib.load(open("config/params.toml", "rb"))
try:
    v = sqlite3.connect(cfg["general"]["db_path"]).execute(
        "SELECT MAX(version) FROM schema_version").fetchone()[0]
    print(v)
except Exception as e:
    print(f"?({e})")
PY
)"

echo "--- journal depuis le redémarrage ---"
journalctl -u "$SERVICE" --since "$SINCE" --no-pager \
  | grep -E "migrations appliquées|démarrage —|Added job|ALERTE" || true

echo ""
echo "✅ Déploiement terminé — schéma en version $VERSION, service actif."
echo "   Suivre les cycles : journalctl -u $SERVICE -f"
