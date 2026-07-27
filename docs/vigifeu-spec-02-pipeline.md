# Vigifeu — Spécification 02 : Pipeline d'ingestion et de qualification

**Version :** 0.4
**Références :** cadrage v0.3, Spec 01 (modèle de données)
**Périmètre :** phase pré-SaaS. Le pipeline écrit dans SQLite (un seul processus écrivain, mode WAL), archive en Parquet, et déclenche la régénération statique (Spec 04).

---

## 1. Principes du pipeline

**P1 — Événementiel, pas continu.** Les données arrivent par paquets (passages satellite, publications quotidiennes). Le pipeline est une boucle de cycles courts qui ne fait un travail lourd que lorsqu'une nouveauté est détectée.

**P2 — Idempotent et rejouable.** Relancer un cycle sur des données déjà vues est un no-op (clé d'unicité de `hotspot_raw`). Rejouer tout l'historique brut avec un algorithme amélioré reconstruit l'interprétation à l'identique ou en mieux (Spec 01, P1/P2) — le rejeu sur l'archive du feu de Saumos est le **test de référence** de toute évolution.

**P3 — Jamais bloquant sur une source.** Chaque source a ses timeouts, retries et quotas ; l'échec d'une source n'empêche pas le traitement des autres. Constaté en prototype : FIRMS ralentit précisément les jours de grands feux.

**P4 — Tout paramètre est configurable et versionné.** Fenêtres de clustering, seuils de qualification, paliers de distance : une table/fichier de configuration versionné, référencé par `qualification_reason` et les versions de FireEvent. Aucune constante magique dans le code.

**P5 — Chaque cycle est journalisé** (`ingestion_run`) : ce qui a été demandé, obtenu, en combien de temps, avec quelles erreurs. La boîte noire du système.

---

## 2. Ordonnancement

| Tâche | Fréquence | Rôle |
|---|---|---|
| `fetch_firms` | toutes les 15 min | jour courant (J) + contrôle J-1, par satellite actif de `satellite_source` |
| `fetch_firms_backfill` | horaire | re-demande des jours à trous (`ingestion_run` en échec) jusqu'à J-7 |
| `fetch_weather_obs` | toutes les 15 min | météo constatée pour chaque FireEvent `actif` qualifié végétation ; déclenche le recalcul des relations `direction_vent` (§7) et la régénération associée (§8) |
| `fetch_weather_forecast` | toutes les 3 h | prévisions (pluie, vent) pour feux actifs + communes concernées |
| `fetch_drought` | quotidien (matin) | FWI + sous-indices EFFIS, Météo des forêts ; SIM à la décade |
| `fetch_vigieau` | quotidien | arrêtés de restriction |
| `fetch_mtg_fir` | toutes les 10 min *(phase 2)* | produit FIR EUMETSAT, niveau de confiance `probable` |
| `process_cycle` | après chaque fetch ayant produit de la nouveauté | chaîne de traitement §3 |
| `archive_sweep` | quotidien (nuit) | export Parquet des jours/feux clos, purge de la fenêtre glissante SQLite |

La fréquence de 15 min sur FIRMS sert deux objectifs à la fois : fraîcheur, et **mesure de la latence NRT** (première apparition vs heure d'acquisition — cadrage §6bis) sans aucun code supplémentaire.

---

## 3. Chaîne de traitement d'un cycle

Déclenchée quand un fetch a inséré au moins un hotspot nouveau.

```
1. normalisation        → lignes CSV → hotspot_raw (insertion idempotente, ingested_at=now)
2. construction passages → rattachement des nouveaux hotspots à un overpass (satellite + fenêtre ±30 min)
3. marquage sources fixes → hotspot dans le rayon d'une fixed_source confirmée ⇒ fixed_source_id, exclu de l'étape 4
4. clustering spatio-temporel → assignation aux FireEvents existants / création / fusion (§4)
5. qualification         → évaluation des règles trois signatures sur chaque FireEvent touché (§5)
6. version               → nouvelle fire_event_version (géométrie, FRP du passage, dédup, mesures §6)
7. cellules              → mise à jour fire_cell_state (first/last acq, état recalculé)
8. relations communes    → intersection, distances, direction du vent ; ouverture/fermeture des fe_commune_rel (§7)
9. régénération          → émission de la liste des pages impactées vers le générateur (§8)
```

Les étapes 4–8 ne traitent que les FireEvents touchés par le cycle — jamais de recalcul global en fonctionnement nominal.

---

## 4. Clustering spatio-temporel

Leçon du prototype (cadrage §7bis) : le clustering purement spatial sur données cumulées fusionne des événements distincts (cas des 12,6 km / 2 jours absorbés dans Saumos). L'algorithme retenu est un **rattachement incrémental par passage** :

### 4.1 Algorithme

Pour chaque nouveau hotspot non marqué source fixe, dans l'ordre des passages :

1. **Candidats** : FireEvents dont la dernière détection date de moins de `T_gap` **et** dont la géométrie (ou un hotspot) est à moins de `D_link` du nouveau hotspot.
2. **Un candidat** → rattachement.
3. **Aucun candidat** → création d'un FireEvent embryonnaire (`qualification` initiale selon §5, pas de `public_id`).
4. **Plusieurs candidats** → **fusion** : les FireEvents candidats sont fusionnés en un seul (le plus ancien absorbe), les absorbés reçoivent `lifecycle=fusionne` avec pointeur `merged_into` ; leurs versions sont conservées (relecture honnête : « deux départs distincts se sont rejoints le … » est une information, pas un bug).

### 4.2 Paramètres initiaux (config versionnée, P4)

| Paramètre | Valeur de départ | Justification |
|---|---|---|
| `D_link` | 1 500 m | ~4 pixels VIIRS ; valeur du prototype, satisfaisante sur juillet 2026 |
| `T_gap` | 48 h | 2 jours sans détection ⇒ nouvel événement ; couvre 4 fenêtres de passage + aléas nuageux |
| `D_link_grands_feux` | 2 500 m | au-delà de `N_hs_grand` hotspots, le front peut sauter (sautes de feu) |
| `N_hs_grand` | 100 | seuil « grand feu » |

### 4.3 Reprises

**Règle unifiée** : tout rattachement d'un hotspot à un FireEvent qui n'est pas `actif` déclenche le traitement de reprise (retour à `actif`, nouvelle version, note `reprise=true`), quelle que soit la fenêtre — ceci couvre aussi la zone de recouvrement 24–48 h où un feu est `plus_detecte` (`T_silence` dépassé) mais encore candidat au rattachement (`T_gap` non écoulé).

Un hotspot proche (< `D_link`) d'un FireEvent en `plus_detecte` depuis moins de `T_reprise` (init : 7 jours) **rouvre** donc l'événement. Au-delà de `T_reprise` : nouvel événement, avec relation `proche_de` vers l'ancien (contexte de fiche : « un feu a parcouru cette zone il y a N jours »). Formulation d'affichage : factuelle, jamais prédictive (§4.1 du cadrage).

### 4.4 Ce que le clustering ne décide pas

La **date de première détection** (`first_acq_at`) est celle du cluster spatio-temporel, recalculée à chaque fusion/scission — donnée contractuelle. En cas de fusion, la fiche publique explicite les deux origines.

### 4.5 Transitions de cycle de vie

Évaluées à chaque cycle (et par une passe horaire pour les feux sans nouveauté) :

| Transition | Règle | Paramètre |
|---|---|---|
| `actif → plus_detecte` | aucune détection depuis `T_silence` | init : 24 h (~2 groupes de passages VIIRS manqués ; à revoir avec MTG) |
| `plus_detecte → actif` | reprise (§4.3), `reprise=true` | `T_reprise` = 7 jours |
| `plus_detecte → archive` | `T_reprise` écoulé sans détection | déclenche l'export Parquet et la dernière régénération (Spec 01 §6) |
| `→ fusionne` | absorption (§4.1), terminal | pointeur `merged_into` + ligne `fe_fe_rel` |

Conséquences associées : `fetch_weather_obs` cesse pour un feu qui quitte `actif` ; le passage en `archive` retire le feu de la carte nationale (il reste accessible par sa fiche et l'historique).

---

## 5. Qualification — règles des trois signatures

Évaluée à chaque version, sur l'historique complet du FireEvent. Ordre d'évaluation strict ; la première règle satisfaite décide ; `qualification_reason` stocke la règle et les valeurs mesurées.

**Tous les comptages de hotspots des règles ci-dessous portent sur les comptages dédupliqués inter-satellites** (§6) : un brûlage vu par deux satellites à 17 min d'écart reste 1–2 pixels physiques, pas 4 — sans cette règle, l'ajout de NOAA-21 dégraderait mécaniquement la qualification.

**Économie sur les suspects** (cf. Spec 01 §4.2) : les événements `suspect_*` ne sont pas versionnés à chaque cycle ; leurs compteurs d'évidence (jours distincts, emprise max, FRP médian) sont mis à jour en place, une version n'étant créée qu'au changement de qualification.

### 5.1 Règles

**R1 — Source fixe (persistant-fixe) ⇒ `suspect_source_fixe`**
`jours_distincts ≥ 3` **ET** `emprise_max ≤ E_fixe` **ET** `frp_median_unitaire ≤ F_fixe`
Valeurs initiales : `E_fixe = 1 200 m` (les complexes industriels multi-torchères dépassent 500 m — constaté), `F_fixe = 8 MW`.
Un `suspect_source_fixe` stable sur `N_promotion = 15` jours distincts devient **candidat `fixed_source`** (revue avant confirmation ; croisement Corine Land Cover à l'appui — code CLC 121/131 renforce, code forêt infirme).

**R2 — Détection isolée (éphémère-unique) ⇒ `suspect_isole`**
`n_passages_avec_detection = 1` **ET** `n_hotspots ≤ 2`.
Jamais publié. Si le passage suivant redétecte à proximité → réévaluation complète (peut devenir un vrai départ).

**R3 — Feu de végétation (persistant-mobile) ⇒ `vegetation_confirme`**
`n_passages_avec_detection ≥ 2` **ET** (`extension_spatiale_entre_passages ≥ E_mobile` **OU** `n_hotspots ≥ N_franc`)
Valeurs initiales : `E_mobile = 400 m`, `N_franc = 8` (un feu franc dès son premier passage — 8+ pixels — est publiable sans attendre la confirmation de mouvement).

**R4 — Par défaut ⇒ `suspect_isole` conservé en observation.**

**Rétrogradation** : un `vegetation_confirme` peut être requalifié `faux_positif` (revue manuelle ou règle) ; sa page publique, si elle existait, affiche la correction explicitement (« requalifié le … ») — jamais de suppression silencieuse (rigueur §8.3 du cadrage).

### 5.2 Publication

Seuls les `vegetation_confirme` reçoivent un `public_id` et une page. Les signaux de niveau `probable` (MTG seul, phase 2) apparaissent **sur la carte nationale uniquement**, sans `public_id` ni fiche, avec leur libellé dédié (Spec 03 §5) ; ils obtiennent page et identifiant à la confirmation VIIRS. Le seuil de publication est distinct du seuil d'alerte future (les abonnés pourront choisir d'être notifiés dès `probable` sur leurs sites — hors périmètre v1).

### 5.3 Boucle d'amélioration

Chaque saison : rejeu de l'historique brut avec les règles courantes, comparaison aux vérités terrain disponibles (BDIFF, EMS, presse), mesure des taux de faux positifs/négatifs, ajustement des paramètres. La config étant versionnée, chaque fiche sait avec quelles règles elle a été qualifiée.

---

## 6. Mesures factuelles par version

Calculées à l'étape 6 du cycle, uniquement entre **passages comparables** :

* `frp_total_last_pass_mw` : somme des FRP du dernier passage, dédupliquée inter-satellites (déduplication **par passage** : les hotspots de satellites différents dont les fenêtres se chevauchent à < 20 min et les pixels à < 375 m partagent un `dedup_group` ; un seul compte dans les totaux) ;
* courbe d'intensité : séries nuit/nuit et jour/jour séparées (sensibilité capteur différente — cadrage §7ter) ;
* `front_progress_km` / `front_bearing_deg` : déplacement du bord d'attaque (cellules nouvellement détectées) entre deux passages de même type ; affiché « le front a progressé de N km vers le NORD en H h » uniquement si `H` et le type de passage sont cohérents ;
* `area_ha_estimee` : enveloppe × facteur de remplissage — catégorie `estimee`, toujours affichée comme estimation.

---

## 7. Relations feu ↔ commune

Recalculées à chaque version, par intersection de géométries (jamais centroïde) :

| `rel_type` | Règle | Paramètres init |
|---|---|---|
| `emprise_dans_commune` | intersection enveloppe ∩ contour commune non vide | — |
| `a_moins_de_X` | distance enveloppe → limite communale ≤ palier | paliers 5 / 10 / 20 km |
| `direction_vent` | commune (partiellement) dans le secteur angulaire `±A_vent` autour de la direction vers laquelle le vent souffle, à ≤ `D_vent` | `A_vent = 30°`, `D_vent = 15 km` |

Cycle de vie : relation absente → création (`valid_from`) ; relation qui cesse → fermeture (`valid_to`), jamais suppression. `direction_vent` est recalculée à chaque nouvelle `weather_obs` (fait composé, double horodatage affiché — cadrage §4.1), **avec hystérésis** : ouverture dès la première mesure dans le secteur, fermeture seulement après `N_hysteresis = 3` mesures consécutives hors secteur (~45 min) — sans quoi un vent oscillant autour de la limite du secteur ±30° ferait entrer/sortir une commune à chaque cycle (inflation de relations, régénérations en rafale, fiche qui clignote).

---

## 8. Déclenchement de la régénération statique

En fin de cycle, le pipeline émet la liste minimale des pages impactées (via `regen_queue`, Spec 01 §3.8) :

* carte nationale : si au moins un FireEvent publié a changé ;
* fiches feux : les FireEvents avec nouvelle version **ou** nouvelle `weather_obs` ;
* fiches communes : les communes dont une `fe_commune_rel` a été ouverte ou fermée (y compris `direction_vent` sur simple changement de vent) ou dont le feu associé a une nouvelle version ;
* fiches communes « rien à signaler » : régénérées **une fois par jour, après `fetch_drought` du matin** (leur bloc « contexte du jour » doit refléter la publication quotidienne de Météo-France/EFFIS) — jamais en cycle courant ;
* passe nocturne : sitemaps et maintenance uniquement.

Le générateur (Spec 04) consomme cette liste ; le pipeline n'écrit jamais de HTML lui-même.

---

## 9. Robustesse des sources

* **FIRMS** : requêtes jour par jour, timeout 180 s, 3 tentatives espacées, respect du quota (budget de transactions par tranche de 10 min, priorité au jour courant) ; tout échec journalisé et rattrapé par `fetch_firms_backfill`.
* **Open-Meteo / EFFIS / autres** : mêmes principes ; une source en panne dégrade la page (« météo momentanément indisponible, dernière mesure : … ») sans jamais la bloquer. Rappel licence (cadrage §5.8) : l'offre gratuite d'Open-Meteo est réservée au non-commercial — abonnement ou bascule Météo-France à trancher au lancement ; le `provider` étant un champ de `weather_obs`, le changement de source est transparent pour le modèle.
* **Trous de collecte** : un trou non rattrapé > 24 h déclenche une alerte interne (mail/log) — le silence d'une source est une information critique en saison.
* **MTG (phase 2)** : ingestion dans `geo_detection_raw` (Spec 01 §3.8), confiance `probable` ; la promotion `probable → confirme` se fait à la confirmation VIIRS dans la même zone (fenêtre 24 h, rayon 3 km).

---

## 10. Tests de validation du pipeline

1. **Rejeu Saumos** (jeu de référence archivé) : le pipeline doit produire — sans intervention — un FireEvent unique dont `first_acq_at = 2026-07-22 12:32 UTC`, l'événement du 20/07 à 12,6 km restant distinct ; les 4+ communes concernées ; la progression ~5,5 km nord entre les nuits du 24 et du 25 ; la chute d'intensité nuit/nuit d'un facteur > 10.
2. **Rejeu 7 jours France** : Grande-Synthe, Fos, Port-Jérôme, triangle mosellan qualifiés `suspect_source_fixe` ; aucun feu réel connu de la période rétrogradé.
3. **Idempotence** : double ingestion d'un même jour = zéro nouvelle ligne, zéro nouvelle version.
4. **Panne simulée** de FIRMS pendant 6 h : rattrapage complet par backfill, trou visible dans `ingestion_run`, aucune page affichant une fraîcheur mensongère.

---

## 11. Points à trancher (entrées de la Spec 03 — fiches et affichage)

1. Seuil exact de publication (R3) vs risque d'afficher des brûlages agricoles persistants ; option : croisement CLC « terres arables » comme rétrogradeur.
2. Distance enveloppe→commune : géodésique simple (v1) vs distance à la limite communale précise (coût de calcul, gain de justesse).
3. Représentation de la fusion de feux sur les pages publiques (chronologie à deux origines).
4. Budget de transactions FIRMS partagé entre jour courant et backfill les jours de crise.
5. Fenêtre et rayon de la confirmation VIIRS d'un signal MTG (24 h / 3 km proposés).

---

*Prochain module : Spécification 03 — Fiches feu et commune (contenu, libellés contractuels conformes §4.1, niveaux de lecture, traduction métier), puis Spécification 04 — Générateur statique & SEO/GEO.*
