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

### 2.2 Ordre des sources

1. **OpenStreetMap** — en premier (largeur + rapidité). Voie : extrait **Geofabrik France** (`.osm.pbf`)
   filtré par tags (`tourism=camping`, `amenity=school`, `amenity=hospital`, `amenity=fuel`, …), ou
   **Overpass OSM** pour des mises à jour ciblées. ⚠️ à **ne pas confondre** avec `engine/overpass.py`
   (= passages satellites) ; l'ingestion OSM est du code neuf. Licence **ODbL → attribution obligatoire**.
2. **BD TOPO (IGN)** — couche « points d'activité/intérêt » (santé, enseignement, sport/loisir dont
   campings). **Officielle, fraîche, licence Etalab** ; GeoPackages départementaux (même mécanique
   d'import qu'Admin Express, Lot 3).
3. **ICPE / Seveso via Géorisques** (`georisques.gouv.fr`, open data) — sous-ensemble à **fort enjeu**,
   pertinent pour la cible « exploitants de sites » (Spec 05 §4).

### 2.3 Fraîcheur et déduplication (P5 — responsabilité)

- provenance + `imported_at` par POI ;
- **cadence de ré-import** définie par source (OSM plus volatil que BD TOPO) ;
- **déduplication inter-sources** : un même camping présent dans OSM **et** BD TOPO ne doit compter qu'une
  fois (rapprochement spatial + catégorie).

---

## 3. Couplage feu ↔ POI

**Réutilise `engine/relations.py`** (Lot 3) : STRtree en Lambert-93, proximité à l'**union des cellules**
du feu (pas le hull), rayons {emprise, <5, <10, <20 km} — exactement comme les relations feu↔commune.
**Aucune machinerie spatiale nouvelle.**

- Table `fe_poi_rel` — sœur de `fe_commune_rel` : (`fire_event_id`, `poi_id`, `rel_type`,
  `valid_from`, `valid_to`), historisée open/close comme les relations communales.
- Câblage dans `process_cycle` (nouvelle étape, à côté du calcul des relations communales).
- Émission vers `regen_queue` du feu concerné quand ses relations POI changent (comme les communes, Lot 3).

---

## 4. Affichage public — prudent, jamais nominatif riche

- Les POI sont **contextuels à un feu**, **pas** un calque national exhaustif (bruit, poids).
- **Sur la fiche feu publique** (Spec 03 §3.3, « POI majeurs sur la carte du feu ») : les POI majeurs
  près de l'emprise, sur la carte + une **phrase de qualification** agrégée.
- **Décision (Spec 05, 2026-07-31) : au public, enjeu PRUDENT / AGRÉGÉ, PAS de nominatif riche/chiffré.**

Lexique contractuel (Spec 03 §1 P3) :

| Situation (donnée) | Libellé public autorisé | Interdit au public |
|---|---|---|
| POI sensibles à proximité | « {N} campings et {M} établissements scolaires dans un rayon de {5} km » | « camping *{nom}*, {3 500} places, à {2} km » |
| Aucun POI sensible détecté | « Aucun établissement sensible recensé à proximité » (+ réserve de fraîcheur) | affirmer une absence sans réserve |

- L'**enjeu nommé + chiffré** (« camping de 3 500 places à 2 km ») est une **feature abonné** (Spec 08),
  sous limitation de responsabilité contractuelle. Même partage gratuit/payant que la courbe MTG (Spec 05 §2.7).
- Sur la **carte de fiche** : marqueurs de POI majeurs (agrégés / catégorie), pas d'étiquette nominative.
- La page méthodologie documente la **réserve de fraîcheur** (esprit « plus détecté ≠ éteint », Spec 03 §1 P6).

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

1. **Migration `poi`** (table §2.1) + **`fe_poi_rel`** (§3). Une migration, rien d'autre.
2. **Importeur OSM** — `referentiels/poi_osm.py` : extrait Geofabrik filtré ou Overpass OSM, upsert
   idempotent par (`source`, `source_ref`). Fixture sur la bbox Gironde-ouest (Lot 3, déjà là).
3. **Relation feu↔POI** — extension de `engine/relations.py` + `fe_poi_rel` historisée, câblée dans
   `process_cycle`, émission `regen_queue`.
4. **Qualification agrégée sur la fiche** — lexique `fr.phrase_enjeux_poi(counts)` (§4). **Public prudent
   seulement.** Golden Saumos régénéré.
5. **POI majeurs sur la carte du feu** (Spec 03 §3.3) — marqueurs agrégés.
6. **2ᵉ et 3ᵉ sources** — importeur BD TOPO (dédup vs OSM), puis Géorisques/ICPE-Seveso.
7. **Imagerie — cran léger** : calque GIBS daté sur la carte de fiche (§5).
8. **Fraîcheur** — cadence de ré-import + provenance datée (§2.3).

**Jalon J-POI :** un feu réel affiche un enjeu public agrégé correct (rejeu Saumos : « N campings dans
5 km »), relations `fe_poi_rel` cohérentes, golden vert.

---

## 7. Questions ouvertes

- `OUVERT` : jeu de catégories POI exact de la v1 (au-delà de camping/école/hôpital/station-service/ICPE).
- `OUVERT` : rayon(s) affichés au public (5 km ? plusieurs paliers ?) et seuil « POI majeur » sur la carte.
- `OUVERT` : cadence de ré-import par source (§2.3).
- `OUVERT` : cran 2 imagerie Sentinel-2 (traitement de scène, stockage).
