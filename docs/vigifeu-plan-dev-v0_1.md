# Vigifeu — Plan de développement

**Version :** 0.1
**Références :** cadrage v0.4, Specs 01–04
**Périmètre :** construction du socle pré-SaaS (ingestion → moteur → site statique), jusqu'à la mise en ligne publique et la préparation de la phase 2 (MTG).

---

## 1. Choix techniques arrêtés

Principe directeur : **le minimum de pièces mobiles**. Le système est un processus d'ingestion, une base SQLite, des fichiers Parquet et un dossier de HTML statique. Tout choix qui ajoute un serveur, un service ou une dépendance native doit se justifier.

### 1.1 Stack

| Domaine | Choix | Justification / alternative écartée |
|---|---|---|
| Langage | **Python 3.12+** | Continuité avec le prototype ; écosystème géo/data sans équivalent (shapely, pyarrow, duckdb). Écarté : Go/Rust — prématuré, le goulot est la donnée, pas le CPU. |
| Gestion de projet | **uv** (pyproject.toml, lockfile) | Reproductibilité des environnements dev/prod, rapidité. |
| HTTP | **httpx** + **tenacity** (retries/backoff) | Timeouts fins par source, retries déclaratifs — exigences Spec 02 §9. |
| Base vivante | **SQLite mode WAL**, accès via le module standard `sqlite3` | Décision du cadrage §8.4. Pas d'ORM : le schéma Spec 01 est du SQL explicite, et la migration PostGIS sera une réécriture de requêtes de toute façon — un ORM masquerait sans dispenser. |
| Migrations de schéma | **Scripts SQL numérotés** + table `schema_version` | Alembic écarté : surdimensionné sans ORM, une centaine de lignes de runner maison suffit. |
| Géométrie | **Shapely 2 + pyproj**, STRtree en mémoire pour les intersections ; stockage WKT dans SQLite | SpatiaLite écarté en v1 : dépendance native fragile pour un gain nul à notre volume (~35 000 communes tiennent en RAM dans un STRtree, requêtes en millisecondes). Le schéma reste identique ; PostGIS reprendra le rôle du STRtree en cible SaaS. |
| Archive | **pyarrow** (écriture GeoParquet) + **DuckDB** (requêtes) | Décision du cadrage §8.4. DuckDB lit aussi SQLite : les requêtes trans-fenêtres (vivant + archive) sont possibles sans ETL. |
| Ordonnancement | **Un seul processus daemon** avec **APScheduler**, sous **systemd** | Contrainte structurante : un seul écrivain SQLite (WAL). Cron écarté : plusieurs crons = plusieurs écrivains potentiels + gestion de verrous. Un daemon unique porte toutes les tâches de Spec 02 §2, systemd assure redémarrage et journald. |
| Gabarits | **Jinja2** | Décision Spec 04 §2. Lexique = module Python versionné (`lexique/fr.py`), les gabarits n'assemblent que ses fonctions. |
| Config | **TOML versionné dans le repo** (`config/params.toml`) | Tous les paramètres de Spec 02 (D_link, T_gap, seuils R1–R3, hystérésis…) ; le hash de la config entre dans `qualification_reason`. |
| Frontend | **HTML/CSS artisanal, zéro framework, JS vanilla minimal** | Spec 04 P3 (contenu complet sans JS) rend tout framework contre-productif. JS limité à : durées relatives, repli des données brutes, carte. |
| Cartes | **MapLibre GL JS** + fond **Protomaps (PMTiles) auto-hébergé** | Les tuiles OSM publiques interdisent l'usage soutenu (pic médiatique = bannissement le pire jour). Un fichier PMTiles France (~2–3 Go) servi par Nginx en range requests : zéro dépendance externe, zéro coût variable, cohérent avec « le site tient le pic sans toucher à la base » (Spec 04 §3). |
| Serveur | **VPS unique** (Hetzner/OVH, 4 Go RAM suffisent largement) + **Nginx** | Ingestion + génération + service des statiques sur la même machine en v1. |
| CDN | **Cloudflare (offre gratuite)** devant Nginx dès le lancement | Tranche le point ouvert Spec 04 §10.2 : coût nul, absorbe le pic médiatique, invalidation ciblée par URL via API. |
| CI/CD | **GitHub Actions** : pytest, lint lexique (grep termes interdits), validation JSON-LD, budget perf, golden file Saumos ; déploiement par rsync du site + restart systemd | Les 5 garde-fous de Spec 04 §9 sont des jobs CI, pas des intentions. |
| Tests | **pytest** ; fixtures = jeux de données réels archivés (Saumos, 7 jours France) | Le rejeu Saumos (Spec 02 §10) est le test d'intégration canonique. |
| Monitoring | **journald + healthchecks.io** (ping de chaque tâche planifiée) + alerte mail sur trou de collecte > 24 h | L'alerte « silence d'une source » de Spec 02 §9 est un ping manquant — pas d'infrastructure de monitoring dédiée en v1. |
| Analytics | **Plausible ou Matomo** auto-hébergé, sans cookie | Décision Spec 04 §7, cohérence RGPD. |

### 1.2 Points ouverts des specs — tranchés pour la v1

| Point ouvert | Décision v1 | Motif |
|---|---|---|
| `fire_cell_state` par version vs état courant (Spec 01 §9.3) | **État courant**, reconstruction depuis `hotspot_raw` si besoin | Léger ; P1/P2 garantissent que rien n'est perdu. Réversible au premier volume réel. |
| Distance enveloppe→commune (Spec 02 §11.2) | **Géodésique simple** (shapely sur géométries projetées Lambert-93) | Le gain de la distance « à la limite précise » est déjà obtenu par l'intersection sur contours réels ; coût nul. |
| Dédup inter-satellites (Spec 01 §9.2) | **Par passage** (fenêtres < 20 min, pixels < 375 m) | Retenu par défaut en Spec 02 §6, confirmé. |
| Image Open Graph (Spec 04 §10.1) | **Image générique par département** en v1 | Le rendu carte serveur (headless browser ou staticmaps) est un chantier à part ; reporté en v1.1. |
| Cône de vent (Spec 03 §7.2) | **Secteur angulaire ±A_vent** (30°) | Cohérence visuelle avec la relation `direction_vent` — le dessin montre exactement ce que la relation calcule. |
| Pages départements (Spec 04 §10.3) | **Listes simples** | Retenu par défaut. |
| Maille FWI EFFIS (Spec 01 §9.5) | **Point de grille brut**, agrégat calculé à l'affichage | P1/P2 : le brut d'abord, l'agrégat est du code. |
| Relais des communiqués officiels (Spec 03 §7.4) | **Saisie manuelle** possible dès la v1 (table `official_statement`, catégorie `declaree`) | Coût faible, débloque les libellés « fixé/maîtrisé (source préfecture) » sur les grands feux. |
| Seuils DC (Spec 03 §7.1) | Validation littérature **pendant le Lot 4**, avant première publication | Tâche documentaire, pas de blocage amont. |

### 1.3 Choix reportés (avec échéance)

* **Open-Meteo payant vs Météo-France open data** : décision au lancement commercial (cadrage §5.8). L'architecture rend le choix indolore (`provider` en champ). Rien à faire d'ici là.
* **Licence EUMETSAT Service Provider** : démarche administrative à lancer **en parallèle du Lot 4** (délais administratifs = chemin critique de la phase 2, pas du socle).
* **PostGIS** : à la signature du premier client multi-sites ou à la construction de l'espace abonné. Aucun code v1 ne doit l'anticiper au-delà du schéma propre.

---

## 2. Découpage en lots

Logique d'ordonnancement : **la collecte d'abord, tout de suite** — la saison 2026 est en cours, et chaque jour sans ingestion est de la latence non mesurée et des données NRT qui ne se reconstituent pas à l'identique (P5 du modèle : `ingested_at` ne se rejoue pas). Le reste suit l'ordre des dépendances : socle données → moteur → référentiels → site.

### Lot 0 — Amorçage et collecte d'urgence *(semaine 1)*

Objectif : **le magnétophone tourne**, même si rien ne l'exploite encore.

* Repo, pyproject/uv, config TOML initiale, CI squelette (pytest vide qui passe).
* VPS provisionné, systemd en place.
* Ingestion FIRMS minimale : `fetch_firms` 15 min → `hotspot_raw` + `ingestion_run` (satellites SNPP, NOAA-20, NOAA-21 en config), idempotence par clé d'unicité, timeouts/retries/quota.
* Constitution de la **fixture Saumos** : téléchargement archive FIRMS 20–27 juillet 2026 France, gel en Parquet dans le repo de test (+ le jeu « 7 jours France » du prototype).

**Jalon L0 :** la latence NRT est mesurée en continu (`ingested_at − acq_at` requêtable) ; la fixture de référence existe.

### Lot 1 — Socle données complet *(semaines 2–4)*

* Schéma SQLite intégral Spec 01 (observations, interprétation, référentiels), runner de migrations.
* `overpass` (construction des passages), `fetch_firms_backfill`.
* Fetchers météo : `weather_obs` (Open-Meteo, gratuit en phase R&D), `weather_forecast`, `fetch_drought` (EFFIS FWI + sous-indices, Météo des forêts), `fetch_vigieau`.
* `archive_sweep` : export Parquet quotidien partitionné, purge fenêtre glissante, règle « jamais purger un hotspot d'un feu non archivé ».
* Monitoring : pings healthchecks par tâche, alerte trou > 24 h.

**Jalon L1 :** 100 % des tâches de Spec 02 §2 (hors MTG) tournent en production ; test d'idempotence (Spec 02 §10.3) vert ; panne simulée 6 h rattrapée par backfill (§10.4).

### Lot 2 — Moteur d'interprétation *(semaines 4–8, le cœur)*

* Clustering spatio-temporel incrémental par passage (Spec 02 §4) : rattachement, création, **fusion** (merged_into, fe_fe_rel), **reprises** (règle unifiée §4.3), transitions de cycle de vie (§4.5).
* Déduplication inter-satellites par passage (`dedup_group`).
* Qualification trois signatures R1–R4 (Spec 02 §5), `qualification_reason` avec hash de config, économie de versionnage des suspects, promotion `fixed_source` (candidats après 15 jours — la revue manuelle est un simple CLI en v1).
* `fire_event_version` : géométrie (hull), FRP par passage, mesures nuit/nuit et jour/jour, `front_progress_km/bearing`, `area_ha_estimee`.
* `fire_cell_state` (grille ~750 m, état courant).

**Jalon L2 (le jalon du projet) : rejeu Saumos vert** — un FireEvent unique, `first_acq_at = 2026-07-22 12:32 UTC`, l'événement du 20/07 distinct, progression ~5,5 km nord, chute d'intensité ×10 (Spec 02 §10.1). Et rejeu 7 jours France : sources industrielles en `suspect_source_fixe`, aucun vrai feu rétrogradé (§10.2).

### Lot 3 — Référentiels et relations communales *(semaines 8–10)*

* Import **Admin Express** (géométries généralisée + précise, millésime), `commune`, `commune_succession` ; précalculs surface forestière (BD Forêt ou CLC).
* Import **BDIFF** (2006+, France) ; Prométhée en option si le format le permet sans friction (sinon v1.1).
* `fe_commune_rel` : intersection STRtree, paliers 5/10/20 km, `direction_vent` avec **hystérésis** (3 mesures), ouverture/fermeture historisée.
* `regen_queue` alimentée en fin de cycle (le générateur n'existe pas encore : la file s'accumule, c'est un test grandeur nature).

**Jalon L3 :** sur le rejeu Saumos, les 4+ communes ressortent avec les bons types de relation et des intervalles de validité cohérents avec la chronologie réelle des évacuations.

### Lot 4 — Générateur, fiches, site *(semaines 10–16)*

Le plus gros lot en volume, parallélisable en trois chantiers :

* **Chantier lexique** : bibliothèque `lexique/` (toutes les fonctions de Spec 03 §2), barèmes de traduction versionnés, validation des seuils DC contre la littérature. Testable unitairement sans HTML.
* **Chantier gabarits** : composants partagés (badges, blocs météo/latence/attributions/limites), fiche feu (Spec 03 §3, y compris mode archive et états particuliers §5), fiche commune (§4, y compris « rien à signaler »), carte nationale, pages liste départements, méthodologie, mentions légales/CGU, 404.
* **Chantier générateur** : consommateur de `regen_queue`, écriture atomique par lot, GeoJSON par page, MapLibre + PMTiles, JSON-LD, sitemaps segmentés, canonicals/301, flux Atom, robots.txt + llms.txt, invalidation Cloudflare ciblée.
* CI complète : **lint du lexique** (termes interdits = build en échec), **golden file Saumos**, validation JSON-LD/W3C, budget < 100 ko, grep inverse horodatage de génération.

**Jalon L4 :** site généré de bout en bout depuis la production réelle ; la fiche Saumos (archive) est conforme au golden file approuvé ; mise en ligne **bêta privée** (URL non indexée) pour revue.

### Lot 5 — Mise en ligne publique et exploitation *(semaines 16–18)*

* Ouverture de l'indexation par vagues : communes concernées par des feux 2026 → communes BDIFF riches → extension progressive (Spec 04 §5).
* Search Console, tableau de bord crawlers (DuckDB sur logs Nginx), analytics.
* Page méthodologie finalisée avec **latences mesurées depuis le Lot 0** (l'argument chiffré promis au cadrage §15bis).
* Rodage : une à deux semaines d'observation en conditions réelles, ajustement des paramètres (config versionnée → chaque ajustement est traçable).

**Jalon L5 : lancement public.**

### Lot 6 — Phase 2 : géostationnaire *(après le socle ; démarches dès le Lot 4)*

* Licence EUMETSAT Service Provider (administratif, lancé en amont).
* Test technique : produit FIR 22–25 juillet 2026 vs déroulé VIIRS Saumos (le test identifié au cadrage §5.2).
* `geo_detection_raw`, `fetch_mtg_fir` 10 min, affichage `probable` carte nationale seule, promotion à confirmation VIIRS (24 h / 3 km), révision des seuils de cellules calés VIIRS.

---

## 3. Chemin critique et risques

| Risque | Impact | Parade |
|---|---|---|
| Démarrer la collecte tard | Latence non mesurée sur la saison 2026, argument méthodologie affaibli | **Lot 0 en semaine 1, avant tout le reste** — c'est la seule vraie urgence calendaire. |
| Paramètres de clustering faux sur des cas non vus (reliefs, archipels de brûlages) | Fusions/scissions erronées → `first_acq_at` faux (donnée contractuelle) | Le rejeu permanent (fixtures + saison accumulée) fait partie de la CI ; la config versionnée rend chaque ajustement rejouable. |
| Fond de carte : PMTiles France plus lourd/complexe que prévu | Retard Lot 4 | Repli sans douleur : MapLibre + tuiles raster d'un fournisseur gratuit à quota (MapTiler free tier) le temps de finir le PMTiles. |
| Délais administratifs EUMETSAT | Retard phase 2 | Démarche lancée dès le Lot 4, en parallèle — n'est jamais sur le chemin du socle. |
| Un seul VPS = un seul point de panne | Indisponibilité en saison | Le site est un dossier statique : réplique froide = rsync + DNS. Cloudflare sert le cache pendant la bascule. Acceptable en v1, documenté. |
| Dérive du périmètre (espace abonné, alertes) | Le socle ne sort jamais | Le cadrage §8.6 l'a tranché : pas d'abonnés en v1. Toute demande « et si on ajoutait » se relit contre cette section. |

**Chemin critique nominal : L0 → L1 → L2 → L3 → L4 → L5, ~18 semaines** pour un développeur à temps plein (à dilater proportionnellement sinon). Le Lot 2 est le seul dont la durée est réellement incertaine (algorithmique) ; les Lots 1 et 4 sont volumineux mais prévisibles.

---

## 4. Organisation du repo (proposition)

```
vigifeu/
├── pyproject.toml, uv.lock
├── config/
│   └── params.toml          # tous les paramètres Spec 02, versionnés
├── migrations/              # 001_init.sql, 002_…
├── src/vigifeu/
│   ├── ingest/              # fetch_firms, weather, drought, vigieau (+ mtg en phase 2)
│   ├── model/               # accès SQLite, entités, archive parquet
│   ├── engine/              # clustering, qualification, versions, cellules, relations
│   ├── lexique/             # fr.py — les seules chaînes affichables du système
│   ├── generate/            # gabarits jinja2, geojson, jsonld, sitemaps, atom
│   ├── scheduler.py         # daemon APScheduler (unique écrivain)
│   └── cli.py               # rejeu, revue fixed_source, saisie official_statement, backfill manuel
├── templates/               # *.html.j2
├── static/                  # css, js minimal, pmtiles
└── tests/
    ├── fixtures/            # saumos/, france-7j/ (parquet gelés)
    └── …                    # unit + rejeux + lint lexique
```

---

## 5. Définition de « fini » (socle)

Le socle est terminé quand, simultanément :

1. les 4 tests de validation du pipeline (Spec 02 §10) et les 5 garde-fous du générateur (Spec 04 §9) sont verts en CI ;
2. le site public est en ligne, indexé par vagues, avec méthodologie chiffrée par les mesures de la saison ;
3. un feu réel survenu pendant le rodage a été suivi de bout en bout sans intervention manuelle (détection → fiche → communes → archive) ;
4. la démarche EUMETSAT est engagée et le test FIR/Saumos réalisé — la phase 2 peut démarrer sur un terrain déblayé.
