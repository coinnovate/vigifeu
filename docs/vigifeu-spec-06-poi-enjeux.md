# Vigifeu — Spécification 06 : POI / enjeux & imagerie d'étendue

**Version :** 0.1
**Références :** Spec 05 (§0 principe de responsabilité P0, §1 principes, §3-§3bis cadrage), Spec 01
(§1 principes, modèle `commune`), Spec 03 (§1, §3.3 fiche feu), Spec 04 (générateur), cadrage v0.4
(§5.5, §6bis, §15bis), Lot 3 (référentiels + `engine/relations.py`)
**Périmètre :** le **référentiel POI / enjeux** (public) et l'**imagerie satellite d'étendue** — deux
enrichissements de la **fiche feu publique**, **faisables immédiatement** (aucun verrou externe), qui
prolongent le socle des Lots 3-4. Le **site déclaré** de l'abonné et les **notifications** relèvent de la
future Spec 08 (couche abonné) ; cette spéc pose le référentiel public qui les alimentera.

**Statut :** premier bloc exécutable de la phase 2 (Spec 05 §5). Le POI est la **fondation** : il qualifie
tout feu (public) et servira de socle au couplage site déclaré ↔ enjeu (abonné). L'imagerie est un
enrichissement indépendant, hors chemin critique.

**Rappel P0 (Spec 05 §0), opposable ici :** toute qualification d'enjeu **nominative et chiffrée** engage
la responsabilité → elle est réservée au **côté contractualisé** (abonné). Le **public** reste **prudent /
agrégé** (§4). La **fraîcheur** d'un POI (camping fermé, école déplacée) est un enjeu de responsabilité (§5).

---

## 1. Deux objets à ne pas confondre

| | **POI référentiel** (cette spéc, public) | **Site déclaré** (Spec 08, abonné) |
|---|---|---|
| Origine | OSM + BD TOPO + Géorisques | l'abonné déclare **son** site |
| Portée | public — qualifie **tout** feu | privé — cœur du modèle éco (cadrage §15bis) |
| Où ça sert | fiche feu publique (§4) | espace abonné + notifications |

Un site déclaré sera souvent « un POI que l'abonné a marqué comme sien ». Les deux se **couplent** au feu
par la **même machinerie** (§3), construite ici une fois.

---

## 2. Sources et import

Même pattern que le Lot 3 (Admin Express, BDIFF) : télécharger de gros fichiers open data, importer de
façon **idempotente** dans une table `poi`, avec provenance et date.

### 2.1 Table `poi` (référentiel, mis à jour par millésimes)

Sœur de `commune`. Esquisse (à figer à la migration) :

| Champ | Type | Description |
|---|---|---|
| `id` | INTEGER PK | interne |
| `source` | TEXT | `osm` / `bdtopo` / `georisques` |
| `source_ref` | TEXT | clé naturelle dans la source (ex. `node/12345`) |
| `category` | TEXT | `camping` / `ecole` / `hopital` / `station_service` / `icpe` / `seveso` / … |
| `nom` | TEXT NULL | interne ; **non affiché au public** en v1 (§4) |
| `geom_wkt` | TEXT | point WGS84 (WKT), cohérent avec `commune` |
| `enjeu` | TEXT/JSON NULL | attributs (capacité, seuil Seveso…) — usage abonné, pas public v1 |
| `imported_at` | TEXT (ISO UTC) | date d'import (P5) ; **jamais réécrit** sans changement de source |

Clé d'idempotence = (`source`, `source_ref`). Ré-import = upsert.

### 2.2 Ordre des sources — où trouver les données

Catégories v1 (Spec 05 §4, §7) : campings, écoles, hôpitaux/EHPAD, stations-service, ICPE-Seveso.

| Catégories | Source | Où télécharger | Format / licence |
|---|---|---|---|
| campings, écoles, hôpitaux/EHPAD, stations-service | **OpenStreetMap** | Overpass API (ciblé) ou extrait **Geofabrik France** | GeoJSON / `.osm.pbf` — **ODbL** (attribution) |
| santé, enseignement, PAI (dont campings) | **BD TOPO (IGN)** | `data.geopf.fr/telechargement/resource/BDTOPO` | GeoPackage v3.5 — **Etalab** |
| ICPE / Seveso | **Géorisques** | `georisques.gouv.fr/donnees/bases-de-donnees/installations-industrielles` | CSV + Shapefile — **Licence ouverte** |

1. **OpenStreetMap** — en premier (largeur + rapidité).
   - **Overpass API** (`https://overpass-api.de/api/interpreter`) — requête ciblée, sort du **GeoJSON**
     direct, léger (voie de démarrage, comme la fixture Lot 3). Tags v1 : campings `tourism=camping` ;
     écoles `amenity=school` ; hôpitaux/cliniques `amenity=hospital` ; EHPAD `amenity=social_facility`
     + `social_facility=nursing_home` ; stations-service `amenity=fuel`.
   - **Geofabrik** (`download.geofabrik.de/europe/france.html`) — `france-latest.osm.pbf` (~4 Go) ou
     sous-extraits régionaux (ex. Nouvelle-Aquitaine), à parser en **pyosmium** ; pour volume/robustesse.
   - ⚠️ à **ne pas confondre** avec `engine/overpass.py` (= passages satellites) ; l'ingestion OSM est du
     code neuf. Licence **ODbL → attribution obligatoire**.
2. **BD TOPO (IGN)** — thème « Services et activités » (couches `etablissement_de_sante`, `enseignement`,
   `point_d_activite_ou_d_interet`). **Même plateforme `data.geopf.fr` qu'Admin Express** (Lot 3 : on
   connaît le pattern d'URL et les gotchas 7z/`chmod`). GeoPackage départemental, licence **Etalab**.
3. **ICPE / Seveso via Géorisques** — sous-ensemble à **fort enjeu** (cible « exploitants de sites »).
   CSV + Shapefile, **mise à jour quotidienne**, inclut Seveso seuil haut/bas. ⚠️ champ **précision de
   géolocalisation** variable (parfois centroïde commune, parfois adresse géocodée) → à filtrer/qualifier
   (P5). Aussi sur data.gouv.fr (« Base des installations classées (ICPE) »).

**Démarrage (étape 2 du dev, §6) :** OSM via **Overpass sur la bbox Gironde-ouest** (celle du Lot 3) →
petite fixture GeoJSON, comme pour les communes. BD TOPO + Géorisques en sources 2-3.

### 2.3 Fraîcheur et déduplication (P5 — responsabilité)

- provenance + `imported_at` par POI ;
- **cadence de ré-import** définie par source (OSM plus volatil que BD TOPO) ;
- **déduplication inter-sources** : un même camping présent dans OSM **et** BD TOPO ne doit compter qu'une
  fois (rapprochement spatial + catégorie).

---

## 3. Couplages spatiaux — deux relations distinctes

**Réutilise `engine/relations.py`** (Lot 3, STRtree en Lambert-93) — **aucune machinerie spatiale nouvelle.**
Deux couplages, pour deux surfaces :

**3.1 Feu ↔ POI** (fiche feu) : proximité à l'**union des cellules** du feu (pas le hull), rayons
{**emprise**, <5, <10, <20 km} — exactement comme les relations feu↔commune. Le palier `emprise` = POI
**dans la zone détectée** (traité à part, §4).
- Table `fe_poi_rel` — sœur de `fe_commune_rel` : (`fire_event_id`, `poi_id`, `rel_type`, `valid_from`,
  `valid_to`), historisée open/close comme les relations communales.
- Câblage dans `process_cycle` (à côté des relations communales), émission `regen_queue` du feu quand ses
  relations POI changent.

**3.2 Commune ↔ POI** (fiche commune) : **point-dans-polygone** — quels POI sont **dans** chaque commune,
pour le recensement permanent des enjeux (§4). Indépendant des feux.
- Table `commune_poi` : (`code_insee`, `poi_id`) — quasi-statique, recalculée à l'import d'un référentiel
  POI ou communes (comme les relations quasi-statiques du Lot 3), pas à chaque cycle.
- Contre le STRtree communes déjà en place.

---

## 4. Affichage public — prudent, jamais nominatif ni impact affirmé

**Trois surfaces, trois traitements** (les POI sont **contextuels**, jamais un calque national exhaustif) :

- **Carte nationale : NON** — bruit, poids, et ce serait « la carte des sites vulnérables » en permanence.
- **Fiche feu : OUI** (Spec 03 §3.3) — POI proches ou dans l'emprise (`fe_poi_rel`), sur la carte
  (**marqueurs individuels par catégorie**, icône, sans étiquette) + **phrase de qualification agrégée**
  (comptes). La carte montre les positions ; le texte reste agrégé.
- **Fiche commune : OUI** — **recensement agrégé permanent** des enjeux de la commune (`commune_poi`, §3.2),
  au même titre que l'historique BDIFF et le contexte sécheresse (valeur hors-saison / SEO).

**Décision (Spec 05, 2026-07-31) : au public, enjeu PRUDENT / AGRÉGÉ, PAS de nominatif riche/chiffré, et
JAMAIS d'impact affirmé.** Le palier le plus fort (« dans la zone détectée ») exige le **plus** de prudence :
l'emprise = union de **cellules de détection grossières** ; un POI dedans signifie *une cellule où il y a
eu détection recouvre son emplacement*, **pas** qu'il a brûlé. Affirmer un dommage sur un établissement
(nommé de surcroît) qu'on ne peut vérifier au satellite est l'over-claim que P0 interdit. La fraîcheur
(§2.3) compte le plus à ce palier (un camping fermé faussement « dans le feu » = pire cas).

Lexique contractuel (Spec 03 §1 P3) :

| Situation (donnée) | Libellé public autorisé | Interdit au public |
|---|---|---|
| POI dans l'emprise (`rel_type=emprise`) | « Dans la zone détectée du feu : {N} campings, {M} écoles » | « le camping *{nom}* a brûlé / a été atteint / est détruit » |
| POI à proximité (`<5 km`) | « À proximité (moins de {5} km) : {N} campings et {M} établissements scolaires » | « camping *{nom}*, {3 500} places » ; « menacé », « en danger » |
| Aucun POI sensible détecté | « Aucun établissement sensible recensé à proximité » (+ réserve de fraîcheur) | affirmer une absence sans réserve |
| Recensement commune (`commune_poi`) | « Enjeux sensibles recensés dans la commune : {N} campings, {M} écoles, {K} sites Seveso » | tout libellé nominatif ou d'impact |
| Réserve (méthodo + encadré) | « Zone détectée par satellite (cellule ~{X} m) — ne préjuge pas de dégâts. » | affirmer un impact non confirmé |

- L'**enjeu nommé + chiffré** (« camping de 3 500 places à 2 km ») et le nominatif « votre site est dans la
  zone détectée — vérifiez auprès des secours » sont des **features abonné** (Spec 08), sous limitation de
  responsabilité contractuelle. Même partage gratuit/payant que la courbe MTG (Spec 05 §2.7).
- Sur la **carte de fiche** (**décision A, 2026-07-31**) : **marqueurs individuels par catégorie** (icône
  ⛺/🏫/…, **sans nom ni capacité**). Les emplacements viennent de la **donnée publique** (OSM/BD TOPO) ;
  la catégorie seule n'affirme ni nom, ni capacité, ni impact. Le POI **dans l'emprise** est rendu à
  l'intérieur du polygone (distinct des marqueurs alentour), porteur de la **réserve de coarseness**
  (marqueur ≠ dégât). Le **nominatif + capacité + site déclaré précis** restent **abonné** (Spec 08).
- **Visibilité (décision 2026-08-01) : marqueurs POI affichés PAR DÉFAUT** (cohérent avec la phrase agrégée,
  déjà par défaut), avec un **toggle de masquage** (case de légende = visibilité de calque MapLibre) pour
  qui veut une carte épurée. Pas d'opt-in : l'enjeu est la valeur, visible tout de suite (y compris crawlers).
- **Paliers sur la carte (décision 2026-08-01) : `emprise` + `a_moins_de_5km` seulement.** Les paliers
  lointains (10/20 km) restent **dans le texte** (comptes agrégés), pas en marqueurs — sinon un méga-feu
  couvre la carte de marqueurs à 20 km qui n'apprennent rien.
- La page méthodologie documente la **réserve de fraîcheur** et la coarseness de l'emprise (esprit
  « plus détecté ≠ éteint », Spec 03 §1 P6).

---

## 5. Imagerie satellite d'étendue

**Statut :** enrichissement du **socle public gratuit**, **hors chemin critique**, indépendant du POI et de
MTG. Données **libres et gratuites** (aucune licence commerciale).

**Objectif :** montrer visuellement l'**étendue** du feu, en complément des points chauds :
- **pendant** — vraie-couleur avec **panache de fumée** (résolution modeste, quotidien) ;
- **après / pendant** — **cicatrice de brûlure** en fausse-couleur SWIR (Sentinel-2, 10 m), l'étendue
  brûlée nette. C'est le cœur de l'intérêt.

**Sources (gratuites, attribution obligatoire) :**

| Source | Donne | Coût donnée | Effort |
|---|---|---|---|
| **NASA GIBS / Worldview** | vraie-couleur quotidienne (MODIS/VIIRS), tuiles WMTS datées | gratuit | **léger** (calque de tuiles dans MapLibre) |
| **Copernicus Sentinel-2** (Copernicus Data Space Ecosystem, `dataspace.copernicus.eu`) | 10 m, SWIR / NBR | **gratuit** (APIs OData/STAC) | **moyen** (télécharger + composer la fausse-couleur) |

Note : **Sentinel Hub** est un service de commodité commercial (quota gratuit puis payant), **non requis** —
le CDSE donne accès aux scènes gratuitement. L'effort Sentinel-2 est du **dev + compute/stockage**, pas un coût.

**Discipline P0 (honnêteté « veille pas alerte ») :**
- une image est une **observation datée** (Spec 01 P3), catégorie `mesuree` ; **date d'acquisition + source**
  affichées comme toute donnée ;
- **pas de temps réel** (Sentinel-2 repasse ~5 jours) → légende obligatoire « Image du {date} ({source}) —
  l'étendue a pu évoluer depuis » ; jamais laisser croire à un état courant ;
- **nuages** : passage inexploitable → dégradé honnête (« imagerie momentanément indisponible », Spec 03 §1 P6).

**Technique :** réutilise la stack carte (MapLibre + tuiles) ; imagerie déjà géoréférencée, overlay naturel
avec l'emprise.

**Deux crans :**
1. **Cran léger** : calque **GIBS** activable sur la carte de fiche, daté au jour du feu. Intégration minime.
2. **Cran riche** (`OUVERT`, v2) : **instantané cicatrice Sentinel-2** (SWIR) pour les feux **significatifs /
   archivés**. À évaluer : traitement de scène, compute, stockage.

---

## 6. Étapes de développement (petits pas, tests au fil, commits FR)

1. **Migration** — table `poi` (§2.1) + `fe_poi_rel` (§3.1) + `commune_poi` (§3.2). Une migration.
2. **Importeur OSM** — `referentiels/poi_osm.py` : extrait Geofabrik filtré ou Overpass OSM, upsert
   idempotent par (`source`, `source_ref`). Fixture sur la bbox Gironde-ouest (Lot 3, déjà là).
3. **Relation feu↔POI** — extension de `engine/relations.py` + `fe_poi_rel` historisée, câblée dans
   `process_cycle`, émission `regen_queue`. **Distinguer le palier `emprise` (dans la zone) des rayons.**
4. **Relation commune↔POI** — `commune_poi` (point-dans-polygone), recalculée à l'import (quasi-statique).
5. **Qualification agrégée sur la fiche feu** — lexique `fr.phrase_enjeux_poi(counts)` (§4), avec ligne
   distincte « dans la zone détectée » vs « à proximité ». **Public prudent seulement.** Golden régénéré.
6. **Recensement communal sur la fiche commune** — phrase agrégée `commune_poi` (§4).
7. **POI majeurs sur la carte du feu** (Spec 03 §3.3) — marqueurs agrégés par catégorie ; POI dans
   l'emprise rendu à l'intérieur du polygone.
8. **2ᵉ et 3ᵉ sources** — importeur BD TOPO (dédup vs OSM), puis Géorisques/ICPE-Seveso.
9. **Imagerie — cran léger** : calque GIBS daté sur la carte de fiche (§5).
10. **Fraîcheur** — cadence de ré-import + provenance datée (§2.3).

**Jalon J-POI :** un feu réel affiche un enjeu public agrégé correct (rejeu Saumos : « dans la zone
détectée : … » + « à proximité : … »), une fiche commune affiche son recensement d'enjeux, relations
`fe_poi_rel` / `commune_poi` cohérentes, golden vert.

---

## 7. Décisions & questions ouvertes

**Décidé (2026-07-31) :**
- **Catégories v1** : campings · établissements scolaires · hôpitaux/EHPAD · stations-service · ICPE-Seveso.
  Élargissement (villages vacances, ERP, captages…) en v1.1.
- **Affichage** : carte nationale NON ; fiche feu OUI (proximité + dans la zone) ; fiche commune OUI
  (recensement agrégé permanent). Public agrégé, jamais nominatif ni impact affirmé (§4).
- **Hors v1** : enjeux linéaires (axes routiers) et surfaciques (zones urbanisées) — data d'une autre forme.

**`OUVERT` :**
- rayon(s) affichés au public (5 km ? plusieurs paliers ?) et seuil « POI majeur » sur la carte ;
- résolution `{X} m` à citer dans la réserve d'emprise (§4) ;
- cadence de ré-import par source (§2.3) ;
- cran 2 imagerie Sentinel-2 (traitement de scène, stockage).
