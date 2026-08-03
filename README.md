# Vigifeu — socle pré-SaaS

Veille des incendies de végétation. État : **Lot 0** (collecte FIRMS + mesure de latence).
Références : `vigifeu-cadrage-v0_4.md`, Specs 01–04, `vigifeu-plan-dev-v0_1.md` (dans le projet de documentation).

## Prérequis

- Python 3.12+, [uv](https://docs.astral.sh/uv/)
- Une MAP_KEY FIRMS (gratuite) : https://firms.modaps.eosdis.nasa.gov/api/map_key/

## Installation locale

```bash
uv sync --extra dev
cp .env.example .env        # puis renseigner FIRMS_MAP_KEY
uv run pytest -q            # tout doit être vert
```

## Utilisation

```bash
set -a; source .env; set +a          # charger l'environnement

uv run vigifeu init                  # crée data/vigifeu.db, applique les migrations
uv run vigifeu fetch                 # ingère le jour courant (3 satellites)
uv run vigifeu latence               # stats de latence NRT — le jalon L0
uv run vigifeu runs                  # journal d'ingestion (la boîte noire)
uv run vigifeu backfill 2026-07-20 2026-07-27   # ingestion d'un intervalle
```

Daemon (ingestion continue toutes les 15 min) :

```bash
uv run python -m vigifeu.scheduler
```

## Fixture Saumos (jalon L0)

```bash
uv run python scripts/geler_fixture_saumos.py
# → tests/fixtures/saumos/hotspots_2026-07-20_27_france.parquet, à commiter
```

## Déploiement VPS (résumé)

```bash
# sur le serveur, en tant que root :
useradd -r -m -d /opt/vigifeu vigifeu
# déployer le code dans /opt/vigifeu (rsync ou git clone), puis :
cd /opt/vigifeu && sudo -u vigifeu uv sync
cp .env.example .env && $EDITOR .env      # FIRMS_MAP_KEY
cp deploy/vigifeu.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now vigifeu
journalctl -u vigifeu -f                  # vérifier le premier cycle
```

## Mise à jour (routine)

Déploiement courant (code moteur, pipeline, données) — le daemon régénère seul
les pages impactées au redémarrage, rien à générer à la main :

```bash
sudo /opt/vigifeu/deploy/update.sh        # pull + uv sync + restart
```

Après un changement de **gabarit** (`templates/`) ou de **lexique**
(`src/vigifeu/lexique/`), la régénération incrémentale du daemon ne retouche PAS
les pages déjà générées : il faut un **rebuild complet**, daemon arrêté (écrivain
SQLite unique) et **lancé depuis `/opt/vigifeu`** — `params.toml` utilise des
chemins relatifs (`config/…`, `templates/…`, `data/site`), sinon
`PermissionError: … 'config/params.toml'` :

```bash
sudo /opt/vigifeu/deploy/update.sh
sudo systemctl stop vigifeu
sudo -u vigifeu bash -c 'cd /opt/vigifeu && .venv/bin/python -m vigifeu.cli rebuild'
sudo systemctl start vigifeu
```

Un changement **CSS seul** ne nécessite pas de rebuild : `update.sh` suffit (le
restart recopie les assets via `sync_static`).

## Principes non négociables (rappels)

- **Un seul processus écrivain** SQLite (le daemon). Le CLI est sûr tant que le
  daemon est arrêté ou pour de la lecture ; en production, toute écriture passe
  par le daemon.
- Les observations sont **immuables** (Spec 01 P1) ; `ingested_at` n'est jamais
  réécrit — c'est la mesure de latence, elle ne se reconstruit pas.
- Tout paramètre vit dans `config/params.toml`, versionné.
