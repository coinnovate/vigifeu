# Vigifeu — Spécification 07 : Détection géostationnaire MTG

**Version :** 0.1 (cadrage tranché le 2026-08-06 — extraction exécutable de la Spec 05 §2)
**Références :** Spec 05 (§0 P0 responsabilité, §2 cadrage MTG — ce document l'exécute), Spec 01
(P1 immuabilité du socle, P3 double horodatage, P4 catégories, §3.1 `hotspot_raw`, §3.7 `ingestion_run`,
§4.1 `fire_event`), Spec 02 (§2 cycle de vie, §4 clustering, §6 dédup, §9 monitoring), Spec 03 (fiche feu),
Spec 04 (générateur statique SEO, §29/§38 attribution), plan de dev §1.1 (écrivain SQLite unique = le daemon).
**Périmètre :** brancher la **détection géostationnaire MTG** (produit *Active Fire Monitoring*, Data Store
`EO:EUM:DAT:0682`) comme **flux d'observation séparé et immuable** qui **enrichit** les feux du socle
(courbe de tendance sur la fiche) et **surface la détection précoce** (calque « signaux en attente » sur la
carte nationale), sans jamais entrer dans le clustering / la qualification / le `frp_max` VIIRS.
**Ne modifie pas** la fixture golden Saumos ni la suite de tests existante (~333 verts).
**Statut :** **IMPLÉMENTÉE (étapes 1-9) mais NON ACTIVÉE — verdict de validation négatif (2026-08-06).**
Le code est complet et testé (411 verts) ; `[mtg].activated=false`. Voir le **VERDICT** ci-dessous.

---

## ⛔ VERDICT DE VALIDATION (2026-08-06) — 0682 non viable pour la France

Après implémentation complète et activation brève en production, la confrontation à la vraie donnée
**invalide l'usage du 0682 pour la détection sur la France**. Preuves :

- **Feu de Saumos (34 000 ha), jours de pic 22-24/07** : au foyer même, sur une fenêtre 14×14 km,
  `fire_result = 0` **et** `fire_probability = 0,00` sur **tous** les slots. **Zéro signal** — pas même
  sous le seuil de classe.
- **Feux intenses du 06/08** : Piémont (31 MW) et Crau (19 MW) **non détectés**.
- **Ce que le 0682 produit en France** : surtout du **glint solaire côtier de midi** (bande de pixels à
  latitude constante le long des côtes, qui croît/reflue avec le soleil, classe 1 basse probabilité).

**Cause.** MTG est à **0° de longitude** ; à la latitude France l'angle de visée est **très oblique** →
pixel ~2 km étiré, sensibilité et géolocalisation dégradées. Le produit *Active Fire* est calibré pour
l'**Afrique** (quasi-nadir, grands feux de savane), pas pour les feux de forêt européens dont le front
flammant reste **sous le seuil** de détection.

**Décision.** On **n'active pas** (`activated=false`). Le code (ingestion, déprojection geos, confirmation,
candidats, carte « signaux », archive, daemon) est **conservé dormant** : il est correct et testé, c'est la
**donnée** qui est inadéquate. Il se rebranche tel quel si un **produit MTG plus apte** devient disponible et
licencié en public (LSA SAF FRP passé opérationnel, ou un FIR amélioré aux hautes latitudes). Rien à jeter,
verdict acté. Outils de re-vérification : `scripts/mtg_discover.py`, `scripts/mtg_validate_saumos.py`.

---

## 0. Discipline P0 — MTG est l'axe temps, jamais l'axe puissance

Rappel opposable (Spec 05 §0, §2.6). MTG **complète** VIIRS, il ne le remplace ni ne le corrige :

- MTG **n'entre jamais** dans `frp_max`, `frp_sum`, le chiffre « Puissance thermique (FRP) » de la fiche,
  ni dans les règles de qualification R1–R4 (dont R3 `frp_max ≥ f_mobile_mw`). Ces grandeurs restent
  **VIIRS/MODIS uniquement** (comparabilité historique à un seul étalon) ;
- une détection MTG est une **observation immuable** (P1), catégorie native `probable`, horodatée deux fois
  (`acq_at` / `ingested_at`), `ingested_at` **jamais réécrit** (mesure de latence, P3) ;
- elle vit dans une **table sœur étanche** de `hotspot_raw` (§4) : les deux signaux ne se mélangent **jamais**
  en base ;
- **« veille, pas alerte »** tient : un signal MTG seul ne fait pas un feu **public** (§5).

---

## 1. Rôle : l'axe temps, pas l'axe espace

| | Résolution **spatiale** | Résolution **temporelle** |
|---|---|---|
| **VIIRS** (défilant) | fine — 375 m | pauvre — 2 à 4 passages/jour |
| **MTG** (géostationnaire) | grossière — ~1 km nadir, **~2 km à la latitude France** | **continue — une image ~10 min** |

Orthogonaux, pas redondants. VIIRS donne *où précisément* ; MTG donne *le film* entre les passages VIIRS
(intensification, déclin, reprise nocturne) et **le départ plus tôt** qu'un défilant. Honnêteté : à la latitude
France le géostationnaire voit les feux **significatifs** et rate les petits départs — il ajoute la dimension
temps, il ne fait pas de détection fine exhaustive.

---

## 2. Produit et accès

- **Produit : `EO:EUM:DAT:0682` — MTG FCI Level 2 « Active Fire Monitoring » (FIR).** Résolution ~1 km nadir,
  échantillonnage **10 min**, NRT sous ~30 min. Guide : *MTG FCI L2 FIR data guide* (user.eumetsat.int).
- **Format netCDF-4 — VÉRIFIÉ sur un granule réel (2026-08-06), révise l'hypothèse initiale.** Ce n'est
  PAS une liste de pixels (`ListProduct`) mais une **grille pleine-disque 5568×5568 à 2 km** en **projection
  géostationnaire à 0°** : `fire_result` [rows,cols] (classe de détection : 0 pas de feu ; 1/2/3 feu par
  confiance croissante ; 4 hors-disque), `fire_probability` [0-1], `x`/`y` (angles de balayage, radians),
  `mtg_geos_projection` (paramètres). **PAS de FRP** (cf. §3/§6). On sélectionne `fire_result ∈ {1,2,3}`, on
  **déprojette** x,y → lon/lat (pyproj `+proj=geos`), on filtre par bbox France. Le produit est livré en
  **archive SIP (ZIP)** dont on extrait le `.nc`.
- **Accès : API du Data Store** (`api.eumetsat.int` / `data.eumetsat.int`, pull HTTPS). **Pas EUMETCast**
  (antenne). Un daemon tire le netCDF comme on tire déjà FIRMS.
- **Complétude d'ingestion (P3).** Chaque cycle ingère **tous les granules produits depuis le dernier succès**
  (pas seulement le plus récent), sur le modèle de FIRMS qui ingère un jour entier. À cadence 10 min et polling
  15 min, ne prendre que le dernier granule perdrait ~1 slot sur 3 — or la **frise de tendance** (§7) et le
  **comptage de persistance** (§5/§8bis) ont besoin de **chaque** slot. Idempotent (`INSERT OR IGNORE`,
  §4.1) → rejouer un granule connu = no-op.
- **Décision D3 (2026-08-06) — voie d'accès « style maison ».** `httpx` + `tenacity` sur le modèle de
  `ingest/firms.py` (pas la lib `eumdac`), token OAuth2 fait main, parsing netCDF via une lib **légère**
  (`netCDF4`, lecture de la grille + déprojection geos, dézippage du SIP) — **pas** `xarray`. Cohérent, dépendances
  minimales.
- **Auth : OAuth2 `client_credentials`.** `POST {token_url}` avec *consumer key/secret* → *bearer token*
  (~1 h), rafraîchi à l'expiration. Les identifiants sont des **secrets d'environnement**
  (`EUMETSAT_CONSUMER_KEY`, `EUMETSAT_CONSUMER_SECRET`), **jamais dans le dépôt** (même discipline que
  `FIRMS_MAP_KEY`). Sans identifiants → pas d'ingestion MTG (dégradé propre, cf. `activated`).

---

## 3. Licence EUMETSAT — **OBTENUE** (clôt le `OUVERT` de la Spec 05 §2.3)

Réponse du User Service EUMETSAT reçue le **2026-08-06** :

- **`EO:EUM:DAT:0682` (Active Fire) : Free & unrestricted, licence CC-BY-4.0.** **Utilisable en public
  dès maintenant** — c'est le produit que nous ingérons. ⚠️ **Correction (granule réel) : le 0682 ne porte
  PAS de FRP** (contrairement à l'hypothèse initiale tirée d'un résumé de l'ATBD) — c'est de la **détection
  seule** (`fire_result` + `fire_probability`). La frise de la fiche (§7) se base donc sur le **nombre de
  pixels feu** rattachés au fil du temps (proxy d'étendue), et non sur une FRP.
- ⚠️ **À ne pas confondre : le produit *Fire Radiative Power – MTG – 0°* autonome (LSA SAF, EUMETCast) est
  en statut *démonstration* → interdit en environnement opérationnel/public.** Nous **ne l'utilisons pas**.
  (S'il passe « operational », mêmes conditions gratuites — on pourra l'ajouter comme source de FRP affinée.)
- **Aucune redevance dans les deux cas, même pour un service payant (B2B)** → compatible monétisation Phase 2 ;
  la contrainte « MTG doit rester séparable du payant » (Spec 05 §2.3) n'est plus dictée par la licence, mais
  on la **conserve par architecture** (P2).
- **Attribution EUMETSAT obligatoire.** Deux formulations proposées par EUMETSAT. Nous produisons un **dérivé**
  (frise calculée à partir du nombre de pixels feu) → forme « Contains modified ». **Chaîne unique de vérité :
  `[mtg].attribution` (§4.2)**, reprise à l'identique dans les composants du site (Spec 04 §29/§38) :
  > « Contains modified EUMETSAT Meteosat FCI data 2026 »
  (la forme « This service is based on EUMETSAT Meteosat product 2026 » conviendrait pour un usage non modifié).

Référence mémoire : `eumetsat-mtg-licence`.

---

## 4. Modèle de données

**Décision D1 (2026-08-06, précisée) : silo étanche + amorçage via objet léger `geo_candidate`.** MTG est un
flux séparé qui enrichit les feux existants **et** peut amorcer un candidat quand il détecte avant tout VIIRS.
Le candidat est un **objet interne léger (`geo_candidate`), PAS un `fire_event`** : on préserve ainsi
l'invariant « `fire_event` = adossé à des hotspots VIIRS » (le moteur `cells`/`cluster`/`relations`/`versions`
et l'archive supposent tous des `fe_hotspot` — un `fire_event` sans hotspot les casserait). Un `fire_event`
n'apparaît qu'**à la confirmation VIIRS** (créé par le clustering VIIRS habituel), le candidat s'y **rattache**
sans `merged_into` à bricoler.

### 4.1 Table `geo_detection_raw` — sœur immuable de `hotspot_raw` (migration **007**)

Calquée sur `hotspot_raw` (Spec 01 §3.1), **jamais mélangée** avec elle :

```
geo_detection_raw
  id                        INTEGER PRIMARY KEY
  provider                  TEXT NOT NULL      -- 'mtg-fci-fir' (source lisible, pas de FK satellite_source)
  lat                       REAL NOT NULL
  lon                       REAL NOT NULL
  acq_at                    TEXT NOT NULL      -- ISO UTC — heure du slot 10 min (phénomène, P3-a)
  ingested_at               TEXT NOT NULL      -- ISO UTC — 1re apparition chez nous (P3-b, jamais réécrit)
  ingestion_run_id          INTEGER NOT NULL REFERENCES ingestion_run(id)
  frp_mw                    REAL               -- toujours NULL (le 0682 n'a pas de FRP) ; réservé si un produit FRP opérationnel arrive
  frp_uncertainty_mw        REAL
  confidence                TEXT               -- valeur source brute (non normalisée)
  quality_flag              TEXT               -- issu de QualityProduct si exploité
  geo_candidate_id          INTEGER REFERENCES geo_candidate(id)  -- regroupement en candidat (§4.2), NULL sinon
  confirmed_by_fire_event_id INTEGER REFERENCES fire_event(id)    -- posé à la confirmation VIIRS (§5), NULL sinon
  raw_payload               TEXT               -- attributs source du pixel (audit)
  UNIQUE (provider, acq_at, lat, lon)          -- idempotence : réingérer un slot connu = no-op
```

Index : `acq_at`, `ingested_at`, `geo_candidate_id`, `confirmed_by_fire_event_id`. Vue latence NRT dédiée
`v_latence_nrt_mtg` (sur le modèle de `v_latence_nrt`) — le monitoring **est** le schéma. Ces deux colonnes de
liaison sont des **annotations mutables** (comme `overpass_id`/`fixed_source_id` sur `hotspot_raw`) ; elles ne
touchent ni `acq_at` ni `ingested_at` (immuabilité de l'observation préservée).

### 4.2 Table `geo_candidate` — l'amorçage, objet interne léger (jamais public tant que non confirmé)

```
geo_candidate
  id             INTEGER PRIMARY KEY
  created_at     TEXT NOT NULL                        -- ISO UTC
  first_acq_at   TEXT NOT NULL                        -- 1re détection MTG du candidat
  last_acq_at    TEXT NOT NULL                        -- dernière détection rattachée (déclenche la régén, §7)
  centroid_lat   REAL NOT NULL
  centroid_lon   REAL NOT NULL
  n_detections   INTEGER NOT NULL                     -- slots MTG distincts rattachés
  status         TEXT NOT NULL DEFAULT 'en_attente'   -- en_attente | confirme | expire
  fire_event_id  INTEGER REFERENCES fire_event(id)    -- posé à la confirmation VIIRS (promotion), NULL sinon
  CHECK (status IN ('en_attente','confirme','expire'))
```

Un `geo_candidate` **n'a pas de `public_id`, pas de fiche, n'entre dans aucun générateur public** : sa seule
trace publique est le carré « signal en attente » (§8), rendu depuis `geo_detection_raw`, jamais depuis cette
table. Il porte le **suivi interne** et sera le point d'accroche des **notifications B2B** (Spec 08).

### 4.3 Rétention & archive — `geo_detection_raw` grossit vite (10 min)

Même discipline que `hotspot_raw` (section `[archive]`) : **export Parquet partitionné** puis **purge de la
fenêtre glissante**, **jamais** une détection rattachée à un `fire_event` ou un `geo_candidate` encore actif.
Paramètre dédié `[archive].geo_detection_retention_days` (défaut 14, comme `hotspot_retention_days`). Les
`geo_candidate` `expire`/`confirme` suivent l'archive de leurs détections ; un `en_attente` n'est jamais purgé.

### 4.4 Section de configuration `[mtg]` (jamais de constante magique)

```toml
[mtg]
activated = false                 # OFF tant que creds/format non validés en prod (comme drought/bulletins)
collection_id = "EO:EUM:DAT:0682"
token_url = "https://api.eumetsat.int/token"
data_url  = "https://api.eumetsat.int/data"        # à confirmer à l'implémentation
# bbox propre à MTG (clé distincte, initialisée = [general].firms_bbox mais libre de diverger : l'empreinte
# géostationnaire pourrait justifier une marge différente).
bbox = "-5.5,41.0,10.0,51.5"
fetch_interval_min = 15           # cadence de POLLING ; chaque cycle rattrape TOUS les granules 10 min depuis
                                  # le dernier succès (§2), donc aucun slot perdu malgré 15 > 10 min
resolution_m = 2000               # empreinte pixel à la latitude France : taille du carré affiché + rayon de
                                  # regroupement des détections d'un même slot (PAS une dédup inter-satellites)
provider = "mtg-fci-fir"
attribution = "Contains modified EUMETSAT Meteosat FCI data 2026"   # source unique de vérité (§3)
timeout_s = 180
max_retries = 3
retry_wait_min_s = 30
retry_wait_max_s = 300
# Confirmation par VIIRS (§5) — fenêtre spatio-temporelle de rattachement à un fire_event, dans les DEUX sens
confirm_window_h = 24
confirm_radius_km = 3
# Amorçage en geo_candidate (§4.2/§5) : détections MTG persistantes dans le même rayon avant de créer un candidat
seed_min_detections = 3           # ~30 min de persistance à 10 min ; OUVERT à caler sur Saumos
seed_radius_km = 3
# Frise de tendance (§7) — nb minimal de détections rattachées sous lequel on n'affiche PAS de tendance (dégradé)
trend_min_points = 3
display_max_h = 24                # signal en attente retiré de l'AFFICHAGE au-delà (reste en base)
# Anti-cri-au-loup (§8bis) — un signal n'est AFFICHÉ « en attente » que s'il est persistant et hors source fixe
display_min_detections = 2        # slots MTG distincts avant d'afficher un signal isolé (glint/artefact vu 1 fois = rien)
display_mask_fixed_source = true  # masque les signaux dans le rayon d'une source fixe CONFIRMÉE (réutilise fixed_source)
display_fixed_source_radius_m = 2500  # rayon de masquage adapté à l'empreinte MTG ~2 km (≠ [fixed_source].mark_radius_m, calé VIIRS 375 m)
```

Le hash de config (`config_hash`) **n'intègre pas** `[mtg]` : ce flux ne décide **ni** du clustering **ni**
de la qualification VIIRS (`_HASHED_SECTIONS` inchangé) — cohérent avec l'étanchéité §0/§6.

---

## 5. Cycle de vie d'un signal géostationnaire — aucun n'est jeté, et il peut amorcer

Trois destins (Spec 05 §2.5), **augmentés** de l'amorçage `geo_candidate` (D1). Le **rattachement joue dans les
deux sens** (`confirm_window_h` / `confirm_radius_km`) :
- *MTG puis VIIRS* (early-detection) : à l'apparition d'un nouveau `fire_event` VIIRS, on **happe** les
  détections MTG et les `geo_candidate` en attente dans la fenêtre ;
- *VIIRS puis MTG* : une nouvelle détection MTG proche d'un `fire_event` existant s'y **rattache** directement.

1. **Confirmé.** La détection reçoit `confirmed_by_fire_event_id`. L'ensemble des mesures MTG rattachées à un
   feu devient sa **chronologie haute fréquence** → la **frise de tendance** (§7). Si les détections
   appartenaient à un `geo_candidate`, celui-ci passe `status='confirme'`, `fire_event_id` renseigné
   (**promotion** : pas de nouvel objet, pas de `merged_into` — le `fire_event` est celui, normal, créé par le
   clustering VIIRS).
2. **En attente (non encore confirmé).** Détection précoce, jamais « feu », pas de `public_id`, pas de fiche :
   - **signal isolé / pas encore persistant** → carré « en attente » sur la **carte nationale** (v1, §8), sous
     réserve des filtres §8bis, **`display_max_h`** maximum puis retiré de l'affichage (reste en base) ;
   - **candidat amorcé** → dès **`seed_min_detections`** détections dans **`seed_radius_km`** sans VIIRS, on crée
     un **`geo_candidate`** (`status='en_attente'`, §4.2). **Suivi en interne** (base d'une notification B2B
     future, Spec 08), **jamais publié** : sa seule trace publique reste le carré « en attente ». C'est la
     réconciliation P0 — MTG *démarre* le suivi, il ne *publie* pas un feu seul. Un candidat sans confirmation
     au-delà de **`t_reprise_days`** (réutilise `[clustering]`) passe `status='expire'` (gardé, calibration).
3. **Jamais confirmé.** **Gardé** immuable (P1) : **recalage des seuils de détection MTG contre VIIRS** et
   **mesure du taux de faux positifs**. Donnée de calibration, pas déchet.

`OUVERT` (mineur, à caler sur Saumos §10) : `seed_min_detections`, et le cas d'un `geo_candidate` que **deux**
`fire_event` VIIRS distincts pourraient confirmer (rattacher au plus proche centroïde).

---

## 6. Étanchéité des calculs de puissance — MTG n'entre jamais dans l'étalon VIIRS

VIIRS et MTG ne mesurent **pas la même FRP** (empreintes ~375 m vs ~2 km, algorithmes de restitution
différents) : valeurs **non commensurables**. Restent **VIIRS/MODIS uniquement** : `frp_max`, `frp_sum`, le
chiffre « Puissance thermique (FRP) » de la fiche, les règles R1–R4, la comparabilité historique.

**Le 0682 ne fournit AUCUNE FRP** (détection seule, §2/§3) : la question d'un chiffre de puissance MTG ne se
pose donc même pas. Ce que MTG apporte à la fiche est une **tendance relative d'étendue** (nombre de pixels
feu au fil du temps, §7) — indicative, jamais un chiffre comparable à VIIRS. `geo_detection_raw.frp_mw` reste
NULL (réservé au cas où un produit FRP MTG opérationnel arriverait). Deux voies étiquetées, jamais mélangées.

---

## 7. Enrichissement de la fiche feu — le livrable v1

**Décision D2 (2026-08-06, précisée) : la v1 va jusqu'à l'enrichissement de la fiche** (ingestion + affichage
fiche) **et inclut la couche carte nationale « signaux en attente » (§8)** — la détection précoce doit être
visible en public. Seul le **calque MTG coloré sur la carte de la fiche** reste différé (v2).

Sur la fiche d'un feu **confirmé** portant des détections MTG rattachées :

- une **mini-frise d'évolution** en **tendance relative** (« en expansion / stable / en repli »), construite
  à partir du **nombre de pixels feu MTG par slot** (le 0682 n'a pas de FRP, §6), **attribuée EUMETSAT** ;
  **jamais** un axe en MW comparable à la mesure VIIRS ;
- un **fait de fraîcheur** : « détecté aussi par satellite géostationnaire (MTG), 1ʳᵉ vue {HH:MM UTC},
  cadence ~10 min » — c'est la valeur latence rendue visible ;
- la fiche porte donc **deux choses distinctes** : un **chiffre** de puissance (VIIRS, « combien ») et une
  **courbe** d'évolution (MTG, « comment ça bouge »).

**Dégradé honnête (P0, comme l'imagerie/drought).** Sous **`trend_min_points`** détections MTG rattachées, on
**n'affiche pas** de frise (deux points ne font pas une tendance) : mention « pas encore assez de vues MTG pour
une tendance ». Un feu sans aucune détection MTG rattachée ne montre simplement rien de MTG.

**Déclenchement de la régénération.** Une fiche doit être régénérée quand de la donnée MTG s'y attache, **même
sans nouveau hotspot VIIRS**. On **réutilise la `regen_queue` existante** (migration 002, Spec 04 §3, consommée
par `generate/runner.py`) : le rattachement d'une détection enfile `page_type='feu'` (fiche du feu) et
`page_type='carte'` (calque national pour un signal en attente) — **pas de nouvelle machinerie**. Attribution
EUMETSAT dans les composants (Spec 04 §29/§38). `OUVERT` (mineur) : lexique exact des paliers de tendance.

---

## 8. Affichage carte nationale « signaux en attente » — **inclus en v1** (décision 2026-08-06)

Deux couches carte visuellement étanches (Spec 05 §2.7) : « Feux confirmés » (VIIRS, emprise nette, cliquable)
vs signaux MTG en attente (carré ~2 km translucide, pointillé, teinte neutre **jamais** le rouge d'alarme, non
cliquable, `geo_signals.geojson` séparé `interactive:false`, désactivable). Libellé imposé : **« Signal
géostationnaire en attente de confirmation par satellite défilant »**. Alimenté par les `geo_detection_raw`
non confirmées et récentes (< `display_max_h`), qu'il s'agisse d'un signal isolé ou d'un candidat amorcé
(§5) — dans les deux cas la représentation publique est le carré « en attente », **jamais** un feu listé.

**Différé (v2)** : le calque MTG **coloré sur la carte de la fiche** d'un feu (points/carrés datés) — la fiche
v1 se contente de la frise de tendance (§7).

---

## 8bis. Anti-cri-au-loup — le « peut-être un truc par là » ne doit jamais mentir

Le calque « signaux en attente » (§8) ne vaut que s'il **reste crédible**. Un carré qui s'allume toutes les
10 min au même endroit apprend à l'œil à l'ignorer — le signal meurt. MTG voyant **en continu**, il exposerait
exactement les faux positifs déjà connus côté VIIRS (torchères, aciéries : Fos, Dunkerque, Port-Jérôme…) et le
scatter mono-slot (glint solaire, artefacts). **Deux filtres à l'affichage** (pas à l'ingestion — la donnée
brute reste immuable et complète en base, §0/§4) :

1. **Registre des sources fixes.** Une détection MTG dans le rayon d'une **source fixe confirmée** n'est **pas
   affichée** comme « signal en attente ». On **réutilise `config/sources_fixes.toml` + `engine/fixed_source.py`**
   (déjà en place pour VIIRS, cf. mémoire `geofence-sources-fixes`), avec un **rayon propre à MTG**
   (`display_fixed_source_radius_m`, ~2,5 km pour l'empreinte ~2 km — ne jamais réutiliser tel quel le rayon
   VIIRS 375 m). ⚠️ **Ne jamais masquer un vrai feu** : le masquage vise les sources industrielles
   *confirmées*, pas les candidats.
2. **Persistance minimale.** Un pixel vu **une seule fois** (`display_min_detections`) n'allume rien : il faut
   plusieurs slots au même endroit pour qu'« il se passe vraiment quelque chose ». Cohérent avec l'amorçage
   `seed_min_detections` (§5), mais c'est un **seuil d'affichage** distinct (plus bas possible : montrer tôt,
   semer un candidat plus prudemment).

Discipline de fond : ces filtres agissent **uniquement sur l'affichage public**. Une détection masquée reste en
base (calibration, mesure du taux de faux positifs, §5, destin 3). Le carré n'apparaît donc que quand c'est
**nouveau + persistant + hors source connue** — c'est-à-dire quand le « peut-être un truc par là » mérite
vraiment le coup d'œil.

`OUVERT` (à caler sur données réelles) : `display_min_detections`, `display_fixed_source_radius_m`, et le
comportement sur une source fixe encore **suspecte** (non promue) — a priori afficher (prudence : ne pas rater
un vrai feu à côté d'une industrie).

---

## 9. Monitoring — le silence d'une source est une information (Spec 02 §9)

`fetch_mtg_fir` journalise chaque cycle dans `ingestion_run` (`source='mtg:0682'`), comme FIRMS. Dead-man
switch healthchecks.io dédié (URL secrète en env `HEALTHCHECK_MTG_URL`). Un trou de collecte réussie
au-delà de `[monitoring].gap_alert_hours` déclenche l'alerte d'exploitation. **Non bloquant** : un échec MTG
n'interrompt jamais le cycle FIRMS (P3 Spec 02 — jamais bloquant sur une source).

---

## 10. Test technique FIR / Saumos (prérequis avant activation prod)

Valider le produit 0682 sur la chronologie **Saumos (22-25/07)** contre le déroulé VIIRS connu (plan §177) :
détection précoce effective ? cohérence de la tendance MTG avec l'intensification observée ? faux positifs ?
calage de `seed_min_detections` / `confirm_*`. **Ne pas modifier la fixture golden** : le test MTG s'appuie sur
une fixture MTG **propre** (nouveau dossier `tests/fixtures/mtg/…`), à figer une fois un netCDF réel obtenu.

---

## 11. Découpage en étapes (petits pas, tests + commits FR après chacune)

1. **Migration 007 + `[mtg]`** : tables `geo_detection_raw` et `geo_candidate`, index, vue `v_latence_nrt_mtg`,
   `[archive].geo_detection_retention_days`, section config `[mtg]` (`activated=false`). Test de migration (sur
   le modèle `test_migration_006`).
2. **Accès Data Store** : module OAuth2 (`token`, cache/refresh) + client de listing/téléchargement 0682, en
   `httpx`+`tenacity`. Creds en env. Test avec réponses simulées.
3. **Parsing netCDF (grille geos)** : dézippage du SIP, sélection `fire_result ∈ fire_classes`, déprojection
   x,y → lat/lon (pyproj geos), filtrage bbox → (lat, lon, acq_at, classe, probabilité). Fixture synthétique.
   Dépendance `netCDF4` (pyproj déjà présent).
4. **`ingest/mtg.py` — `fetch_mtg_fir`** : cycle idempotent (`INSERT OR IGNORE`, `ingested_at` posé au neuf),
   **rattrapage de tous les granules depuis le dernier succès** (§2), journalisé dans `ingestion_run`. Tests
   d'idempotence, d'immuabilité de `ingested_at`, et de non-perte de slots.
5. **Rattachement / confirmation bidirectionnel** : happer les détections/`geo_candidate` en attente à
   l'apparition d'un feu VIIRS, et rattacher une détection MTG à un feu existant proche
   (`confirm_window_h`/`confirm_radius_km`). Tests des deux sens.
6. **Amorçage via `geo_candidate` (D1)** : créer un candidat léger (`status='en_attente'`, jamais un
   `fire_event`) sur persistance MTG sans VIIRS ; promotion (`status='confirme'`, `fire_event_id`) à la
   confirmation ; expiration au-delà de `t_reprise_days`. Tests des trois branches. ⚠️ **mettre à jour
   `engine/pipeline.py` (wipe/regen)** pour aussi détacher `geo_candidate` (`fire_event_id`→NULL,
   `status`→`en_attente`) lors du wipe des `fire_event`, sinon `foreign_key_check` sort une orpheline.
7. **Enrichissement fiche (v1, D2)** : frise de tendance relative (dégradé sous `trend_min_points`) + fait de
   fraîcheur + attribution ; déclenchement de régén sur `last_acq_at` MTG ; vague dédiée. Tests de génération
   (dont le dégradé « pas assez de vues MTG »).
8. **Couche carte nationale « signaux en attente » (v1, §8 + §8bis)** : génération de `geo_signals.geojson`
   (détections non confirmées < `display_max_h`), **filtrées anti-cri-au-loup** (persistance
   `display_min_detections` + masquage sources fixes confirmées via `engine/fixed_source.py`), calque
   désactivable non cliquable dans `carte.js`, libellé imposé + attribution. Tests : le geojson ne contient
   jamais de `public_id`/feu ; un signal mono-slot ou sur source fixe confirmée n'y figure pas ; un vrai feu
   proche d'une industrie **y figure** (non masqué).
9. **Branchement daemon + monitoring + archive** : job planifié (`fetch_interval_min`), healthcheck dédié, non
   bloquant, + tâche d'export Parquet / purge `geo_detection_raw` (§4.3, sur le modèle de l'archive hotspot).
10. **Calage prod** : obtenir un netCDF réel, figer la fixture MTG, valider sur Saumos (§10), puis `activated=true`.
11. *(v2, différé)* calque MTG coloré sur la carte de la fiche du feu (§8).

---

## 12. Questions ouvertes résiduelles

1. ✅ **RÉSOLU (2026-08-06, granule réel)** — endpoint de recherche `…/data/search-products/os`, téléchargement
   en **SIP (ZIP)**, produit = **grille géostationnaire** (`fire_result`/`fire_probability`, x/y radians), **pas
   de FRP**. Config `[mtg]`/`[mtg.netcdf]` calée en conséquence, parsing réécrit (déprojection geos + dézippage).
2. **Classes de `fire_result` = « feu »** : retenu `{1,2,3}` (le fichier n'a pas de `flag_meanings`) — **à
   confirmer sur Saumos** (§10) contre le déroulé VIIRS connu. Ajustable via `[mtg.netcdf].fire_classes`.
3. **`seed_min_detections` et marge de tendance** — à caler empiriquement sur Saumos (§10).
4. **Promotion candidat MTG → feu public** : au-delà de « confirmation VIIRS », faut-il d'autres corroborations
   (bulletin presse Spec 09, contribution photo Spec 10) ? — à trancher quand l'amorçage tourne.
5. **Fixture MTG réelle** : figer un granule 0682 réel dans `tests/fixtures/mtg/` (le `scripts/mtg_discover.py`
   en télécharge un) pour un test d'intégration bout-en-bout, en complément des fixtures synthétiques.
