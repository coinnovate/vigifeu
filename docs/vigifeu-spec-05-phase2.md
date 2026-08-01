# Vigifeu — Spécification 05 : Phase 2 (géostationnaire & couche commerciale)

**Version :** 0.2 (cadrage tranché le 2026-07-31 ; questions ouvertes balisées `OUVERT`)
**Références :** cadrage v0.4 (§5.4-5.8, §6bis, §8.4-8.6, §11bis, §15bis), Spec 01 (§1, §3.8, §147, §237),
Spec 02 (§4, §33, §137, §189), Spec 03 (§3.3, §4.5, §169), Spec 04 (§29, §38), plan-dev (Lot 6, §1.3)
**Périmètre :** tout ce qui suit le socle public (Lots 0-5, faits et déployés — `sentifeu.fr`).
Deux blocs indissociables : la **détection géostationnaire MTG** (technique) et la **couche
commerciale / SaaS** (monétisation), articulés par le **référentiel POI / enjeux**.

**Statut :** ce document remplace le stub v0.1 et **acte les décisions de cadrage** du 2026-07-31. Les
points non tranchés sont marqués `OUVERT` et doivent l'être avant l'exécution du bloc concerné.

**Structure (décidée le 2026-07-31) :** la Spec 05 est le **chapeau** de la phase 2 (responsabilité,
principes, séquencement, questions ouvertes, rectifications). Chaque bloc devient sa **propre spéc
exécutable au moment où on le construit** : **Spec 06** — POI/enjeux & imagerie d'étendue (rédigée,
premier bloc) ; **Spec 07** — détection géostationnaire MTG (à l'obtention de la licence) ; **Spec 08** —
couche abonné / SaaS (après décisions prix + juridique). Les §2 (MTG) et §4 (abonné) ci-dessous restent
en **cadrage** jusqu'à leur extraction.

---

## 0. Principe de responsabilité — P0, gouverne tout le reste

**Décision structurante (2026-07-31).** Les « alertes » évoquées dans les documents initiaux
deviennent des **notifications B2B contractualisées**, positionnées comme **aide à la décision**
pour des **professionnels** (exploitants de sites, gestionnaires forestiers, assureurs), **jamais**
comme un dispositif d'alerte grand public ni de sécurité des personnes.

Motif : la détection satellitaire est **structurellement faillible** (trous entre passages, nuages,
panache de fumée, feux sous le seuil, pannes de source). Fonder une **responsabilité vie-humaine**
sur ce signal est intenable — et détruirait le positionnement « **veille, pas alerte** » qui fait la
crédibilité du socle public.

Conséquences de conception, opposables à toute la suite :
- notifications **encadrées par contrat** (limitation de responsabilité), disclaimers, assurance
  professionnelle, **jamais** positionnées comme sécurité ;
- l'**absence** de notification ne vaut jamais absence de feu ;
- toute donnée qui **qualifie un enjeu de façon nominative et chiffrée** (POI, capacité) engage la
  responsabilité → elle vit du **côté contractualisé** (abonné), pas du côté public (§3.4) ;
- la **fraîcheur / qualification** des enjeux (cadrage §5.5) n'a de sens qu'en service pro.

---

## 1. Principes structurants de la phase 2

**P1 — Monétisation d'abord (dev), MTG en booster, EUMETSAT en parallèle.** Le développement porte
d'abord la couche commerciale (POI → sites déclarés → notifications → API), qui ne dépend d'aucun tiers.
La **démarche licence EUMETSAT** (chemin critique administratif, hors de notre contrôle) est **lancée en
amont** (fait : mail au User Service Helpdesk envoyé le 2026-07-31). MTG se branche **plus tard**, comme
**multiplicateur de valeur** : c'est le signal continu qui rend l'« aide à la décision » réellement
vendable, pas un pari technique bloquant en tête de file.

**P2 — Deux axes de séparation, étanches.** (a) **VIIRS canonique / MTG complémentaire** : VIIRS/MODIS
reste la mesure de référence (précision spatiale, comparabilité historique) ; MTG apporte l'axe temps et
n'entre jamais dans les calculs canoniques (§2.6). (b) **Public gratuit / abonné payant** : la donnée qui
engage la responsabilité et la valeur commerciale vit du côté abonné ; le public reste prudent. Les deux
séparations obéissent au même réflexe et doivent rester **techniquement séparables** (un repli « gratuit
seulement » doit rester possible sans refonte — cf. §2.3).

**P3 — Le public gratuit est la finalité ; le B2B le finance.** Le socle public (`sentifeu.fr`) est la
mission. La couche commerciale existe pour la soutenir, pas l'inverse. Corollaire : si une contrainte
(coût de licence, risque juridique) rend le commercial non viable, on garde l'enrichissement dans le
public gratuit plutôt que de se ligoter (décision licence, §2.3).

**P4 — Réutilisation du socle avant toute machinerie neuve.** Le référentiel POI réutilise le pattern
d'import des référentiels (Lot 3 : gros fichiers open data → table → upsert idempotent) et le moteur de
relations (`engine/relations.py` : STRtree en Lambert-93, proximité à l'**union des cellules** du feu).
Les principes de la Spec 01 restent en vigueur : observations immuables (P1), double horodatage (P3),
`ingested_at` jamais réécrit, catégorie de donnée explicite (P4), tout en UTC (P7).

**P5 — Fraîcheur = responsabilité (P0 appliqué aux enjeux).** Un POI périmé (camping fermé, école
déplacée) produit une mauvaise qualification → responsabilité. Chaque POI porte sa **source** et sa
**date**, une **cadence de ré-import** est définie, la **déduplication inter-sources** est traitée.

---

## 2. Détection géostationnaire (MTG) — cadrage → future **Spec 07**

*Bloc bloqué sur la licence EUMETSAT (§2.3) et le format netCDF réel : il reste en **cadrage** ici et
deviendra la **Spec 07** exécutable à l'obtention de l'accès (ne pas coder sur des hypothèses de format).*

### 2.1 Rôle : l'axe temps, pas l'axe espace

MTG n'est **pas** « un deuxième détecteur qui trouve des points chauds en plus » (ce serait un complément
jetable, redondant dès le passage VIIRS suivant). Sa valeur est sur un **autre axe** :

| | Résolution **spatiale** | Résolution **temporelle** |
|---|---|---|
| **VIIRS** (défilant) | fine — 375 m | pauvre — 2 à 4 passages/jour |
| **MTG** (géostationnaire) | grossière — ~1-2 km, dégradée à la latitude France | **continue — une image toutes les ~10 min** |

Ils sont **orthogonaux, pas redondants**. VIIRS donne *où précisément* ; MTG donne *le film* — l'évolution
**entre** les passages VIIRS (intensification, déclin, reprise nocturne). Honnêteté : à la latitude France,
le géostationnaire voit bien les feux **significatifs** et rate les petits départs — il ne remplace pas
VIIRS, il ajoute la dimension temps. Cela colle à la cible « exploitants de sites » (on se soucie des feux
significatifs près d'un enjeu), moins à une détection fine exhaustive.

### 2.2 Produit et accès

- Produit : **MTG FCI Level 2 — Active Fire Monitoring**, Data Store `EO:EUM:DAT:0682`, complété par le
  produit **LSA SAF MTG FRP**. Résolution ~1 km nadir, échantillonnage **10 min**, NRT sous 30 min.
- Format **netCDF** : `ListProduct` (pixels feu : position, heure, FRP, incertitude, confiance) +
  `QualityProduct` (drapeaux qualité).
- ⚠️ **Statut « démonstration »** (pré-opérationnel au 2026-07-31) : pas de garantie de continuité ni de
  qualité opérationnelle. Argument supplémentaire pour « MTG plus tard » plutôt qu'en tête de file.
- Voie d'accès : **API du Data Store** (`data.eumetsat.int` / `api.eumetsat.int`, en pull HTTPS) — **zéro
  matériel**, un daemon tire le netCDF comme on tire déjà FIRMS. **Pas EUMETCast** (antenne).

### 2.3 Licence EUMETSAT — chemin critique

- L'usage **public gratuit** tombe probablement sous une **licence gratuite** ; l'usage **commercial**
  (notifications vendues) relève de la licence **« Service Provider »** (redevance + « tiers » à déclarer).
- **Décision (P3) :** le commercial est **éventuel**. Si la redevance est disproportionnée, on conserve
  MTG dans l'**offre publique gratuite** plutôt que de se contraindre. Conséquence d'architecture : MTG
  doit rester **séparable** du chemin des notifications payantes (cohérent avec P2).
- Action lancée le 2026-07-31 : compte EOP (`user.eumetsat.int`) + mail au helpdesk (`ops@eumetsat.int`)
  posant 5 questions (licence usage public gratuit ? redevance commerciale ? impact du statut démo ?
  déclaration des tiers ? attribution d'un dérivé ?), framing commercial = « extension future possible ».
- `OUVERT` : réponse EUMETSAT (licence applicable, coût, contraintes du statut démonstration).

### 2.4 Modèle de données

- Table **`geo_detection_raw`** — sœur de `hotspot_raw` (Spec 01 §147), observation immuable (P1).
  Confiance native `probable`. Colonne **`confirmed_by_fire_event_id`** (FK → `fire_event`, NULL par
  défaut) déjà réservée.
- **Étanche de `hotspot_raw`** : les deux signaux ne se mélangent jamais en base (§2.6).
- `fetch_mtg_fir` : fetcher NRT (~10 min), sur le même modèle que les fetchers existants (`ingested_at`
  posé à l'ingestion, jamais réécrit).

### 2.5 Cycle de vie d'un signal géostationnaire — aucun n'est jeté

1. **Confirmé** (par VIIRS, fenêtre **24 h / rayon 3 km**, Spec 02 §189) → **rattaché** au `fire_event`
   via `confirmed_by_fire_event_id`. Les mesures géostationnaires dans l'emprise deviennent la
   **chronologie haute fréquence** du feu (la « courbe » de §2.7).
2. **En attente** (avant confirmation) → détection précoce. Affiché sur la carte nationale, **24 h
   d'affichage maximum** ; au-delà sans confirmation, retiré de l'**affichage** (reste en base). Côté
   B2B : déclencheur possible de notification `probable` sur un site déclaré, au choix de l'abonné (§4.2).
3. **Jamais confirmé** → **gardé** immuable (P1). Sert au **recalage des seuils de cellules contre VIIRS**
   et à la **mesure du taux de faux positifs**. Ce n'est pas du déchet : c'est la donnée de calibration.

### 2.6 Étanchéité des calculs de puissance — MTG n'y entre jamais

VIIRS et MTG ne mesurent **pas la même FRP** (instruments, empreintes ~375 m vs ~1-2 km, algorithmes de
restitution différents) : leurs valeurs ne sont **pas commensurables**. En conséquence, restent
**VIIRS/MODIS uniquement** :
- `frp_max`, `frp_sum`, le chiffre affiché « Puissance thermique (FRP) » ;
- les **règles de qualification R1–R4** (dont R3 `frp_max ≥ f_mobile_mw`, versionné) ;
- la **comparabilité historique** de tous les feux au même étalon.

Le « recalage des seuils de cellules contre VIIRS » (§2.5) calibre la **détection** MTG (« ce pixel
indique-t-il un feu ? »), **pas** une substitution de valeurs de FRP. Même après calibration, on garde
deux voies étiquetées, jamais un chiffre mélangé.

### 2.7 Affichage

**Deux couches carte visuellement étanches** (jamais mêlées — Spec 03 §154, Spec 02 §137) :

| | Feu confirmé (VIIRS) | Signal géostationnaire en attente (MTG) |
|---|---|---|
| Géométrie | emprise nette (polygone de cellules) | carré grossier ~2 km translucide |
| Couleur | échelle feu (orange→rouge) | teinte neutre atténuée, jamais le rouge d'alarme |
| Trait | plein, opaque | pointillé + hachures, faible opacité |
| Interaction | cliquable → fiche (`public_id`) | non cliquable, infobulle seule |
| Calque | « Feux confirmés » | `geo_signals.geojson` séparé, `interactive:false`, désactivable |

Libellé imposé pour un signal en attente : **« Signal géostationnaire en attente de confirmation par
satellite défilant »** (jamais « feu », pas de `public_id`, pas de fiche).

**Sur la fiche feu** (destin n°1, la valeur) : une **courbe d'évolution** en **tendance relative**
(« monte / se maintient / décline »), attribuée **EUMETSAT / LSA SAF**, **jamais** un chiffre en MW
comparable à la mesure VIIRS. La fiche porte donc deux choses distinctes : un **chiffre** de puissance
(VIIRS, « combien ») et une **courbe** d'évolution (MTG, « comment ça bouge »).

Vague de régénération dédiée + attribution EUMETSAT dans les composants (Spec 04 §29, §38).
`OUVERT` (mineur, à l'implémentation) : lexique exact des paliers de tendance.

### 2.8 Test technique FIR / Saumos

Valider le produit FIR sur la chronologie **Saumos (22-25/07)** contre le déroulé VIIRS connu (plan §177) :
détection précoce effective ? cohérence de la courbe de tendance avec l'intensification observée ? faux
positifs ? Prérequis : accès aux données obtenu (§2.3).

---

## 3. POI / enjeux & imagerie d'étendue — socle public → **Spec 06**

Ces deux enrichissements de la fiche feu publique sont **faisables immédiatement** (aucun verrou externe)
et forment le **premier bloc exécutable** de la phase 2 (§5). Détail dans la **Spec 06 — POI/enjeux &
imagerie d'étendue** ; ci-dessous le cadrage.

**POI / enjeux — la fondation.** Deux objets distincts : le **POI référentiel** (public — OSM → BD TOPO →
Géorisques, qualifie **tout** feu) et le **site déclaré** (abonné, Spec 08). Le couplage feu↔POI **réutilise
`engine/relations.py`** (proximité à l'union des cellules), **zéro machinerie nouvelle**. **Affichage public
PRUDENT / AGRÉGÉ** (« N campings dans 5 km »), **jamais nominatif chiffré** — le nommé + chiffré est réservé
à l'abonné (P0). Fraîcheur = responsabilité (P5).

**Imagerie satellite d'étendue.** Enrichissement du socle public, **hors chemin critique**, données **libres
et gratuites** : NASA GIBS (léger, tuiles prêtes) + Copernicus Sentinel-2 (cicatrice SWIR 10 m, effort de
traitement mais gratuit ; Sentinel Hub **non requis**). Une image = **observation datée** (catégorie
`mesuree`), légende « l'étendue a pu évoluer depuis », dégradé honnête sur nuages. Deux crans : GIBS d'abord,
cicatrice Sentinel-2 en v2.

---

## 4. Couche abonné / SaaS — la monétisation — cadrage → future **Spec 08**

*Bloc gaté par les décisions prix + juridique (§6) : il reste en **cadrage** ici et deviendra la **Spec 08**
exécutable une fois ces préalables levés.*

**Cible B2B première (décision 2026-07-31) : exploitants de sites** (campings, sites industriels/Seveso) —
valeur la plus concrète (cas Saumos/La Grigne), acheteur identifiable, périmètre géographique fini par
client.

### 4.1 Sites surveillés déclarés — cœur du modèle (cadrage §15bis)

L'abonné déclare un ou plusieurs sites (point ou polygone). Chaque site se couple au feu par le moteur de
relations (Spec 06 §3). Portefeuilles de sites par compte.

### 4.2 Notifications B2B (P0)

Dès `probable` (VIIRS suspect, ou signal MTG en attente une fois §2 livré) sur les sites déclarés, **au
choix de l'abonné**. **Contractualisées** (limitation de responsabilité), aide à la décision, jamais
sécurité. L'absence de notification ne vaut jamais absence de feu.

### 4.3 Espace abonné — le premier vrai backend applicatif

Comptes, authentification, tableau de bord, portefeuilles ; **couche dynamique servie hors du cache
statique** (cadrage §8.5, §8.6). Rupture de nature vs le socle actuel (tout est statique généré + Nginx).
`OUVERT` : approche technique (framework, auth, séparation du statique public / dynamique abonné).

### 4.4 API

Presse, assureurs, collectivités, gestionnaires forestiers. `OUVERT` : périmètre, authentification,
quotas, modèle de facturation.

### 4.5 Migration PostGIS

Déclencheur : **signature du premier client multi-sites** ou **construction de l'espace abonné** (§4.3).
Le schéma SQLite a été gardé propre pour ça (plan §1.3, cadrage §8.4).

### 4.6 Enjeu nominatif riche = feature abonné

Le nominatif nommé + chiffré (Spec 06 §4), sous limitation contractuelle, est réservé à l'espace abonné.
C'est la principale valeur ajoutée de qualification vendue.

---

## 5. Séquencement et jalons

```
Fait 2026-07-31 : démarche licence EUMETSAT lancée (chemin critique, en //)
        │
        ▼
[bloc 1] POI référentiel public (OSM → BD TOPO → Géorisques) + affichage prudent sur fiche feu
        │        (enrichit déjà le socle public gratuit ; aucun verrou externe)
        ▼
[bloc 2] Sites déclarés (couplage site ↔ POI ↔ feu)
        │
        ▼
[bloc 3] Espace abonné (auth/dashboard) + notifications B2B + migration PostGIS
        │
        ▼
[bloc 4] API
        ┈┈┈ en parallèle, dès licence EUMETSAT obtenue ┈┈┈
[bloc M] MTG : fetch_mtg_fir, geo_detection_raw, carte étanche, courbe de tendance sur fiche
              (booster de valeur des notifications déjà vendues)
```

**Jalons proposés** (`OUVERT` — à affiner) :
- **J-POI** : un feu réel affiche un enjeu public agrégé correct (rejeu Saumos : « N campings dans 5 km »).
- **J-abonné** : un site déclaré déclenche une notification de proximité de bout en bout (bêta).
- **J-MTG** : signal géostationnaire en attente affiché à part sur la carte nationale + courbe de tendance
  sur une fiche, sur données réelles (dépend de §2.3).

---

## 6. Questions ouvertes — à trancher avant l'exécution du bloc concerné

1. **Modèle de prix B2B et willingness-to-pay** (à valider par de vraies conversations clients avant de
   coder l'espace abonné). Gate le bloc 3.
2. **Préalable juridique** (P0) : avocat, CGU/CGV, limitation de responsabilité, assurance professionnelle.
   Probablement bloquant avant toute vente. Gate les blocs 3-4.
3. **Approche technique de l'espace abonné** (§4.3) : premier backend dynamique du projet.
4. **Réponse EUMETSAT** (§2.3) : licence applicable, coût, contraintes du statut démonstration. Gate le
   bloc M.
5. **Enrichissements de fiche différés** (reportés du cadrage, à replacer ici) : score d'exposition
   structurelle (Spec 01 §237, Spec 03 §4.5), vent au front pour les grands feux (cadrage §5.4), sources
   officielles préfecture/SDIS (Spec 03 §169), partenariat associatif « basse confiance » (cadrage §11bis),
   image Open Graph rendue serveur (plan §1.2), Open-Meteo payant vs Météo-France (cadrage §5.6).

---

## 7. Rectifications de vocabulaire (dette des anciens docs)

Le principe P0 impose que « alerte » **au sens produit client** (le service vendu aux abonnés) devienne
**« notification »** dans les documents antérieurs. **Rectifié le 2026-07-31** : cadrage v0.4 (section
« Notifications » ex-« Alertes », + occurrences produit), Spec 02 (« seuil de notification future »),
Spec 04.

**Préservé volontairement** (autre sens — ne pas toucher) : les **disclaimers** « Vigifeu n'est **pas** un
système d'alerte / d'alerte précoce » (cadrage, Spec 03 §3.10), qui *renforcent* P0 ; les **niveaux
officiels VigiEau** `vigilance / alerte / alerte renforcée / crise` (Spec 01) ; le champ BDIFF
`date_alerte` ; les **alertes internes d'exploitation** « trou de collecte » (plan-dev, Spec 02 §188).
`docs/` reste la source unique.
