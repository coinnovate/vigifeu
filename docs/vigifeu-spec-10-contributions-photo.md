# Vigifeu — Spécification 10 : Contributions photo du public

**Version :** 0.5 (2026-08-07 — mail contributeur optionnel/non vérifié ; modération par mail)
**Références :** Spec 01 (P1 immuabilité du **socle observation**, P4 catégories, §3.1 `hotspot_raw`,
§3.7 `ingestion_run`, §4.1 `fire_event`), Spec 02 (§2 cycle de vie, §9 monitoring — pas de cap silencieux),
Spec 03 (§3.3 fiche feu, §1 libellés), Spec 04 (**générateur statique SEO**), Spec 05 (§0 responsabilité
**P0**), Spec 09 (job non bloquant, contenu tiers non fiable, notification, cadre juridique), plan de dev
§1.1 (**écrivain SQLite unique = le daemon**).
**Périmètre :** un **canal contributif photo public**, en **prise de vue intégrée à l'app** (caméra live, pas
d'upload). Dépôt + affichage (widget) sur **sentifeu.fr** ; le dynamique est porté par une **mini-API
same-origin** (`sentifeu.fr/api/contrib/…`). Parcours : géoloc → feu à proximité (< 10 km, **présélectionné
si on vient d'une page feu**) → **photo prise en direct** → **email optionnel** → **file de modération** →
publication → widget sur la fiche. **Modération par mail** (photo + détails + liens d'action signés). Un
**auto-filtre auto-hébergé** (NSFW + pertinence « feu ») pré-trie avant l'humain. Donnée **`declaree`**,
**horodatée**, de **lignée distincte** du satellite, dans une **base propre**.
**Hors périmètre (spec future) :** tout module temps réel / d'urgence — **chat**, **solidarité**. Voir §13.
**Statut :** cadrage. Aucune ligne de code. À exécuter en petits pas (§12) après validation.

---

## 0. Discipline P0 — une lignée à ne jamais confondre avec le satellite

Le socle Vigifeu est une **observation instrumentale** (FIRMS/Sentinel). Une photo de visiteur est **autre
chose** : un **témoignage humain**, pris **sur place et en direct**, rattaché *par nous* à un feu.

- **attribuée** (« photo prise par un visiteur le {date/heure} ») et **datée**, jamais un constat Vigifeu ;
- catégorie **`declaree`** (Spec 01 P4). N'entre **jamais** dans le clustering, la qualification, le
  `frp_max`, ni aucune métrique du socle ;
- **ne crée pas de feu** : pas de hotspot FIRMS < 10 km ⇒ pas de contribution (§4) ;
- **elle vit dans une base séparée** (§3), ce qui **préserve l'invariant d'écrivain unique** sur la socle.

**Fraîcheur = propriété de première classe.** Un feu bouge en minutes ; une photo est de la **vérité-terrain
périssable**. D'où la **capture in-app en direct** (§2/§4) : `captured_at` (horodaté serveur) et la géoloc
live coïncident avec l'état courant du feu. Le dépôt différé est refusé par construction.

**Immuabilité (dérogation encadrée à P1).** P1 rend le **socle** immuable. Une contribution porte des
**données personnelles** (personnes possibles à l'image, IP, email éventuel) → le RGPD **impose** un cycle de
vie et une purge (§9). Dérogation **cloisonnée** : le socle reste immuable ; **seule la base contributions**
a un cycle de vie.

---

## 1. Cible & principe

**Cible : grand public ouvert, usage mobile-terrain.** Le **canal de collecte** est public (élargissement
assumé au-delà du B2B, 2026-08-05), la **valeur** reste la fiabilisation de la donnée. La **modération** est
le cœur du dispositif ; le service confère un **statut d'hébergeur** (LCEN, §11).

**Rien n'est public sans validation humaine.** Une contribution ne s'affiche nulle part tant qu'un modérateur
ne l'a pas passée à `publiee`. L'auto-filtre (§5) **réduit le volume** — il ne publie **jamais** seul.

**La qualité vient de la modération, pas d'une barrière à l'entrée.** C'est pourquoi le parcours est **sans
friction** (pas de compte, pas de code) : capture live + géoloc + modération suffisent à tenir le déchet.

**Placement du point d'entrée (proéminence calibrée).** Le dépôt n'aboutit que si on est < 10 km d'un feu :
un même gros bouton partout produirait surtout des culs-de-sac (« aucun feu à proximité »). Donc :
- **fiche feu + carte** → **CTA contextuel visible**, feu **présélectionné** (taux de succès maximal) ;
- **partout ailleurs** → **entrée discrète et persistante dans le menu (hamburger mobile)** — découvrable,
  sans promettre un dépôt là où il échouera.
On ne masque pas le bouton selon la proximité (cela exigerait de géolocaliser passivement le visiteur, qu'on
refuse) : on joue sur la **proéminence**, pas sur la présence. Le premier écran **pose le cadre honnêtement**
(« photo prise **maintenant**, **sur place**, près d'un feu détecté ») avant même la géoloc.

---

## 2. Architecture — mini-API same-origin (pas d'espace parallèle)

sentifeu.fr est **statique généré** (Spec 04) : incapable seul de recevoir une image, écrire, inférer,
modérer, envoyer un mail. Ces opérations **exigent un processus serveur**. On n'ajoute **pas** un second
site / une autre origine : un **petit service dynamique sous la même origine**.

```
[ Cloudflare ] ──▶ [ reverse proxy (existant) ]
        │
        ├── sentifeu.fr/  ............... statique généré (fiches SEO)  ──lecture──▶ base SOCLE
        │      ├─ bouton « Déposer une photo » + modal (JS + caméra getUserMedia)
        │      └─ <widget photos> ──fetch (même origine)──▶ /api/contrib/feu/{public_id}/photos
        │
        ├── /api/contrib/*  ............. MINI-API dynamique (même VPS, même origine)
        │      ├─ feux-proches / deposer / signaler
        │      ├─ action/{token}  (liens de modération par mail — GET confirme, POST agit)
        │      ├─ service d'images (route AUTHZ pour les non-publiées)
        │      └─ jobs : auto-filtre ONNX (§5) + purge quotidienne (§9)
        │
        └── /admin/contrib  ............. page de modération (route AUTHENTIFIÉE)
                        │
             base CONTRIBUTIONS (écrivain = l'API)   ──LECTURE seule──▶ base SOCLE
```

**Un seul site, une seule PWA + une mini-API + une page d'admin.** Pas d'univers parallèle.

**Same-origin = simplification.** Pas de CORS ; CSP quasi inchangée. *(`getUserMedia` exige HTTPS — déjà le
cas.)*

**Invariant socle mono-écrivain préservé.** L'API **écrit sa propre base** (`contribution`, `ip_blocklist`)
et **lit la socle en lecture seule** (WAL) pour « feux proches » ; variante : **read-model** exporté par le
daemon (§13). La socle reste **mono-écrivain (daemon)**.

**Auto-filtre & purge = jobs de l'API**, pas du daemon.

**Widget — découplage du générateur.** L'onglet « Photos » est peuplé **côté client**
(`GET /api/contrib/feu/{public_id}/photos`) : générateur statique **non modifié**, **pas de regen** à la
publication, **panne isolée** (API down ⇒ site debout, widget vide). **SEO : widget d'abord** ; « cuisson »
HTML = évolution ultérieure.

---

## 3. Modèle de données — base « contributions » (propre à l'API)

Table principale `contribution` (`declaree`, à cycle de vie) ; table annexe `ip_blocklist`. *(Plus de table
`code` : le mail contributeur est optionnel et **non vérifié**, §4.)*

### 3.1 `contribution`

| Champ | Type | Description |
|---|---|---|
| `id` | INTEGER PK | interne |
| `public_id` | TEXT NULL | identifiant public opaque, **assigné à la publication** (URL de l'image) |
| `fire_event_id` | INTEGER NULL | feu socle rattaché (clé logique, base séparée → pas de FK dure) |
| `hotspot_raw_id` | INTEGER NULL | **ancre géométrique** : hotspot le plus proche retenu (§4) |
| `distance_km` | REAL | distance géoloc-live→hotspot au déclic (audit ; scalaire, **ne localise pas l'auteur**) |
| `captured_at` | TEXT UTC | **instant de la prise de vue**, horodaté **serveur** — pilier fraîcheur (§0) |
| `image_path` | TEXT NULL | chemin du JPEG **hors répertoire public**, encodé par nous (NULL après purge) |
| `image_sha256` | TEXT | empreinte de l'image encodée — **dédup + traçabilité, survit à la purge** |
| `largeur`, `hauteur` | INTEGER | dimensions de l'image encodée |
| `email` | TEXT NULL | **optionnel, non vérifié** — pour prévenir de la publication ; purgé à terme (§9) |
| `ip_hash` | TEXT | HMAC salé de l'IP — anti-abus/blacklist sans conserver l'IP (§8) |
| `consentement_at` | TEXT UTC | **preuve de consentement** (RGPD/LCEN) |
| `cgu_version` | TEXT | **version des CGU/mentions acceptées** (opposabilité) |
| `statut` | TEXT | machine à états (§3.2) |
| `score_nsfw`, `score_feu` | REAL NULL | scores de l'auto-filtre (§5) |
| `auto_verdict` | TEXT NULL | `ok` / `nsfw` / `hors_sujet` |
| `auto_json` | TEXT NULL | détail des scores (audit) |
| `moteur_auto` | TEXT NULL | versions modèles (`nudenet:x;clip:y`) |
| `moderee_par` | TEXT NULL | modérateur — `admin` ou `mail` (LCEN) |
| `motif_rejet` | TEXT NULL | motif de rejet (LCEN) |
| `created_at` | TEXT UTC | écriture de la ligne (= dépôt) |
| `moderee_at` | TEXT UTC NULL | décision humaine |
| `publiee_at` | TEXT UTC NULL | mise en ligne |
| `purge_prevue_at` | TEXT UTC NULL | échéance de purge des rejetées (§9) |
| `purgee_at` | TEXT UTC NULL | purge effective |

*(`captured_at` ≈ `created_at` par construction — la photo est prise puis déposée dans la foulée. On garde les
deux : `captured_at` = sémantique métier ; `created_at` = trace technique.)*

### 3.2 Machine à états (`statut`)

```
deposer (capture + géoloc) ──▶ soumise ──(auto-filtre)──┬─▶ auto_rejetee ──(6 mois)──▶ purgee
                                                         └─▶ a_moderer ──┬─▶ publiee
                                                                         └─▶ rejetee ──(6 mois)──▶ purgee
```

- `soumise` : image encodée + écrite, en attente d'auto-filtre.
- `auto_rejetee` : rejet auto (NSFW/hors-sujet). Non affiché ; vu d'un humain seulement en audit.
- `a_moderer` : passe l'auto-filtre → file humaine (§6) + **mail de modération** (§6).
- `publiee` : exposée par le widget (§7). `public_id` assigné ; si `email`, **notification de publication**.
- `rejetee` : refus humain (motif).
- `purgee` : image + email + `ip_hash` détruits ; **squelette non-perso conservé** (`id`, `image_sha256`,
  `statut` d'origine, `motif_rejet`, dates, `moderee_par`) pour la LCEN (§9).

### 3.3 `ip_blocklist`

`ip_hash` (PK), `motif`, `source` (`manuel`/`auto`), `cree_at`, `expire_at` (blocage **borné**, révisable).

### 3.4 Idempotence / anti-doublon

Index **unique** sur `image_sha256` (même image re-soumise = no-op ; une photo rejetée/purgée garde son hash →
**non re-soumissible**, anti-re-spam voulu). Deux personnes = deux images = deux contributions légitimes.

**Migration** : schéma propre versionné, **indépendant** des migrations socle. *(ex.
`contrib/migrations/001_contributions.sql`.)*

---

## 4. Parcours de dépôt — prise de vue in-app (modal + endpoints)

Bouton **« Déposer une photo »** → **modal**. Étapes :

1. **Géolocalisation** navigateur (obligatoire — c'est l'ancre, §0). Refus/échec → message clair, pas de dépôt.
2. **Feu à proximité** —
   - **page feu** : ce feu est **présélectionné**, **puis vérifié** par la géoloc (< `rayon_max_km`) ;
   - sinon : `GET /api/contrib/feux-proches?lat=&lon=` → `fire_event` dont un `hotspot_raw` < 10 km, triés par
     distance. **Aucun → refus explicite** (fin ; §0) ;
   - **conflit** (contexte = Feu X, géoloc plus proche de Feu Y) : la **réalité physique prime**, on propose Y.
3. **Validation du feu** — on fige `hotspot_raw_id`, `fire_event_id`, `distance_km`. **Position exacte de
   l'auteur jamais stockée** (§0).
4. **Prise de vue en direct** — caméra **dans l'app** (`getUserMedia`, caméra arrière), capture **canvas**.
   **Aucune galerie / fichier** (capture stricte). `captured_at` = déclic, horodaté serveur. *(Image canvas =
   **sans EXIF** → rien à lire/stripper.)*
5. **Email optionnel + consentement** — champ email **facultatif** (« pour être prévenu si votre photo est
   publiée » ; **non vérifié**), + **case de consentement** obligatoire (finalité, conservation, droits ;
   enregistre `cgu_version`).
6. **`POST /api/contrib/deposer`** — corps = **image (blob canvas) + feu choisi + géoloc + email? + consent**.
   Contrôles : **blocklist IP** + **quota IP** (§8). Puis :
   - encodage : resize `max_px` + ré-encodage JPEG (`jpeg_qualite`), `sha256`, écriture **hors répertoire
     public** ;
   - insertion `contribution` en `soumise` (+ `captured_at`, `consentement_at`, `email?`) ;
   - mise en file de l'auto-filtre (§5).

Plus de flux « code » : le dépôt est **en une étape**, sans aller-retour mail → **zéro friction terrain**, et
**plus de vecteur email-bombing** (aucun mail sortant vers le contributeur au dépôt).

**Sécurité endpoints** (Spec 09 §6) : types validés, **taille plafonnée avant lecture mémoire**, email (si
fourni) validé de forme, `lat/lon` bornés au territoire, rate-limit IP (§8), **aucune donnée perso en query
string**. *(Same-origin → pas de CORS.)*

**Compatibilité navigateur mobile (point dur).** `getUserMedia` + géoloc marchent dans le **navigateur mobile
normal** (Safari iOS, Chrome Android) **sans PWA installée**, la seule condition étant **HTTPS** (déjà le cas).
⚠️ **Mais** dans les **navigateurs intégrés des apps sociales** (Facebook/Instagram/LinkedIn, WKWebView),
`getUserMedia` est **souvent bloqué** (surtout iOS) → la caméra ne s'ouvre pas. Il faut **détecter
l'indisponibilité** (`navigator.mediaDevices?.getUserMedia` absent ou permission refusée) et afficher un
**message clair** (« Ouvrez cette page dans Safari/Chrome pour prendre une photo ») — **jamais** un écran
caméra noir. Fallback gracieux, pas de forçage.

---

## 5. Auto-filtre — worker à la demande (job de l'API), auto-hébergé ONNX

**Rôle : pré-trier, pas décider.** Deux briques CPU **auto-hébergées** (l'image **ne quitte jamais le VPS**) :

1. **NSFW / nudité** — `NudeNet` (ONNX, ~200–500 ms/image CPU) → `score_nsfw`.
2. **Pertinence « feu »** — **CLIP zero-shot** (ViT-B/32 ONNX), labels paramétrés (§10) → `score_feu`.

**Verdict** : `nsfw` si `score_nsfw ≥ seuil_nsfw` ; sinon `hors_sujet` si `score_feu < seuil_feu` ; sinon
`ok`. `nsfw`/`hors_sujet` → `auto_rejetee` ; `ok` → `a_moderer`.

**Contrainte VPS (2 vCPU / 4 Go) → worker à la demande, jamais résident :** charge modèles → traite le lot →
libère la RAM (pic ~0,8–1,2 Go transitoire) ; **tout ONNX** ; **1 cœur** (`OMP_NUM_THREADS=1`) ;
**coordination CPU** (ne pas filtrer pendant `fetch_firms`) ; **non bloquant** (timeouts + reste `soumise` +
consigné) ; **rattachement différé** des `soumise` sans `fire_event_id` dès clustering socle. Repli : instance
séparée si le volume explose.

---

## 6. Modération — file admin **+ modération par mail**

**Page admin** `/admin/contrib` (auth) — vue complète : file `a_moderer`, vignette + feu + **`captured_at`** +
**scores auto** + Publier / Rejeter (motif) / Blacklister l'IP. Fraîcheur en aide à la décision (fraîche près
d'un hotspot récent = forte valeur ; près d'un feu ancien = possible reprise, à regarder).

**Modération par mail** (chemin rapide, mobile) — à chaque passage en `a_moderer`, un mail t'est envoyé :
- **la photo** (vignette inline) + **détails** (feu, `captured_at`, distance, scores auto) ;
- **liens d'action** : **Publier / Rejeter / Blacklister l'IP**.

⚠️ **Garde-fou obligatoire (sinon trou de sécu).** Les clients mail et scanners **préchargent les liens** → un
lien qui muterait l'état sur un **GET** s'auto-déclencherait. Donc :
- **tokens signés HMAC, par action, expirants** (`action_token_ttl`) — sinon quiconque a l'URL agit ;
- `GET /api/contrib/action/{token}` **n'a aucun effet** : il **affiche une page de confirmation** ; l'action
  n'est faite que par le **POST** du bouton ;
- **usage unique de fait** : l'action ne s'applique que si `statut = 'a_moderer'` (re-clic = no-op) ;
- `moderee_par = 'mail'` (traçabilité LCEN).

**Commun aux deux chemins :** rien de visible avant `publiee` ; **images non publiées servies uniquement via
route authentifiée** ; `moderee_par`/`moderee_at`/`motif_rejet` renseignés ; **notification de publication**
au contributeur si `email` fourni ; **signalement public** (`POST /api/contrib/signaler`) → repasse en
`a_moderer` / `rejetee`.

*(La modération par mail peut réutiliser l'infra d'envoi de la Spec 09.)*

---

## 7. Exposition sur la fiche — widget

- **Onglet « Photos »** (sentifeu.fr), peuplé **côté client** via `GET /api/contrib/feu/{public_id}/photos`.
  N'apparaît que s'il existe ≥1 `publiee`.
- Chaque photo : image (route API via `public_id`), **`captured_at` en évidence** (« prise le {date/heure} »),
  badge **« Photo de visiteur — non vérifiée par Vigifeu »**. **Aucun nom, aucune géoloc auteur**.
- Contenu tiers non fiable : média statique, **jamais de HTML utilisateur**.
- **Photo publiée = conservée durablement**, feu archivé compris (Spec 02) : archive datée.

---

## 8. Anti-abus — quotas & blacklist IP (axe IP, plus d'email)

Le mail contributeur étant optionnel/non vérifié, l'anti-abus repose sur l'**IP + la capture live + la
modération** (la capture stricte empêche déjà tout envoi scripté en masse) :

- **au dépôt (`deposer`)** : **blocklist IP** (refus, message neutre) + **quota IP** `max_photos_ip_jour`
  (compté sur les lignes `contribution` via `ip_hash`, 24 h glissantes).
- **Blacklist IP** (`ip_blocklist`) : **manuelle** (depuis la file / lien mail) ou **automatique**
  (`auto_block_n_rejets` `auto_rejetee` **ou** `auto_block_n_signalements` sur une `ip_hash` dans une fenêtre).
  `expire_at` **borne** le blocage.

**RGPD** : IP **hachée+salée** (HMAC, secret d'env), **jamais en clair**, **jamais** en query string, durée
bornée (purge §9). Intérêt légitime + conservation **mentionnés** dans la politique de confidentialité. Email
(si fourni) = donnée perso **minimale**, purgée (§9).

---

## 9. Rétention & purge

**RGPD + LCEN** conciliés, par un **job quotidien de l'API** :

- **Rejetées** : purge à **`purge_rejetees_mois` = 6 mois** après décision. **Détruit** `image_path` (fichier
  + colonne), `email`, `ip_hash`. **Conserve** le squelette non-perso (§3.2) → preuve de retrait (LCEN).
  `statut → purgee`.
- **Publiées** : **conservées durablement** (feu archivé compris). **Email purgé** après
  `purge_email_publiee_mois`. Image + squelette restent.
- **`ip_blocklist`** : `expire_at` borné.
- **Job** : suppression disque **effective** + colonnes perso ; consigne (journal propre à l'API — §13) ;
  **idempotent**.

---

## 10. Paramètres (`config/params.toml`, section `[contributions]`)

```toml
[contributions]
activated = false
rayon_max_km = 10.0                  # pas de hotspot en deçà → refus (§4)
max_px = 1600                        # resize de l'image capturée
jpeg_qualite = 82
# Anti-abus (axe IP)
max_photos_ip_jour = 6
auto_block_n_rejets = 5
auto_block_n_signalements = 2
ip_block_expire_jours = 90
# Auto-filtre
seuil_nsfw = 0.85
seuil_feu = 0.30
clip_labels_feu = ["a photo of a wildfire", "a photo of smoke", "a photo of a forest fire"]
clip_labels_hors_sujet = ["a selfie", "a screenshot", "a meme", "an indoor photo", "a document"]
filtre_intervalle_min = 5
filtre_timeout_image_s = 20
filtre_timeout_lot_s = 300
# Modération par mail
action_token_ttl_h = 72              # durée de validité des liens d'action signés
# Notifications
notif_seuil = 1                      # alerte file (canal Spec 09) au-delà de N en attente
# Rétention
purge_rejetees_mois = 6
purge_email_publiee_mois = 3
```

Secrets d'env (hors dépôt) : `CONTRIB_HASH_SECRET` (HMAC IP **et** tokens d'action), SMTP,
`CONTRIB_MODERATION_EMAIL` (destinataire des mails de modération), `HEALTHCHECK_CONTRIB_URL`, auth
`/admin/contrib`.

---

## 11. Cadre juridique (règles dures, opposables)

| Règle | Fondement | Mise en œuvre |
|---|---|---|
| **Rien de public sans validation humaine** | Responsabilité éditoriale ; hébergeur | file + mail (§6) ; l'auto ne publie jamais seul |
| **Signalement + retrait prompt** | LCEN art. 6 | `POST /api/contrib/signaler` + `motif_rejet` + traçabilité |
| **Consentement éclairé + preuve** | RGPD | case obligatoire (§4) ; `consentement_at` + `cgu_version` |
| **Pas de géoloc auteur** | RGPD (minimisation) | position auteur **jamais stockée** ; distance scalaire + GPS **hotspot** |
| **Pas d'EXIF** | RGPD | image canvas = **sans métadonnées** (§4) |
| **Email optionnel & minimal** | RGPD (minimisation) | facultatif, non vérifié, seul usage = notification ; purgé (§9) |
| **IP hachée, durée bornée** | RGPD | HMAC salé ; purge (§9) |
| **Purge des rejetées à 6 mois** | RGPD | job quotidien ; squelette non-perso conservé (LCEN) |
| **Liens d'action mail sécurisés** | Intégrité | tokens signés + confirmation POST (jamais d'effet sur GET, §6) |
| **Personnes/plaques identifiables** | Droit à l'image, RGPD | rejet en modération ; floutage auto = ouvert (§13) |
| **Mineurs** | RGPD | non contrôlable → CGU ; rejet des contenus impliquant des mineurs |
| **Droit d'auteur de la photo** | PI | CGU : garantie d'auteur + licence d'affichage |

**Documents à produire** (hors code, avant `activated = true`) : mention au modal, **politique de
confidentialité**, **CGU de contribution** (versionnées, cf. `cgu_version`), **mentions légales hébergeur**.
Revue juridique recommandée avant ouverture large.

---

## 12. Étapes de développement (petits pas, tests au fil, commits FR)

1. **Squelette API** — service sous `/api/contrib`, base contributions + migration (`contribution` + index
   unique `image_sha256`, `ip_blocklist`), lecture read-only socle. `pytest` vert (8 socle intacts).
2. **Encodage image** — blob → resize + ré-encodage JPEG, `sha256`, écriture hors répertoire public. Test :
   JPEG borné, **sans EXIF**.
3. **Feux proches** — helper « hotspots < `rayon_max_km` → `fire_event` » + endpoint. Test : 5 km → feu ;
   20 km → aucun (refus).
4. **Dépôt** — `POST /deposer` (image + feu + géoloc + email? + consent → `soumise`), quota IP + blocklist.
   Tests : dépôt OK = ligne + `captured_at` ; quota/blocklist = refus ; **image écrite au dépôt uniquement**.
5. **Auto-filtre** — NudeNet + CLIP zero-shot ONNX, worker à la demande, 1 cœur, coordination CPU. **Fixtures
   d'images** → verdict (`nsfw`/`hors_sujet`/`ok`), **sans réseau ni GPU**.
6. **Modération** — page `/admin/contrib` (auth) **+ mail d'action** (tokens signés, page de confirmation,
   POST) : publier/rejeter/blacklister ; notification publication au contributeur ; `signaler`. Tests des
   transitions + **sécurité des liens (GET sans effet)** + **accès images non publiées**.
7. **Purge** — job quotidien ; idempotence + suppression disque effective + squelette conservé.
8. **Widget** — endpoint `feu/{public_id}/photos` + snippet client (affiche `captured_at`) ; dégradation si
   API down. Fiche statique **non régénérée**.
9. **Modal + caméra** — CTA contextuel (fiche/carte) + entrée menu hamburger ; parcours (géoloc → feu
   présélectionné/proche → **capture `getUserMedia`** → email optionnel/consentement → dépôt) ; **détection
   navigateur in-app** → message de repli.

**Jalon J-CONTRIB :** un dépôt (géoloc → feu → capture → `soumise` → auto-filtre `ok` → publication via lien
mail) fait apparaître la photo **datée** dans l'onglet via le widget ; une photo `nsfw` auto-rejetée ; un lien
mail préchargé (GET) **ne publie pas** ; purge à 6 mois OK ; socle intacte mono-écrivain ; 8 tests + nouveaux
verts.

---

## 13. Décisions & questions ouvertes

**Décidé :**
- **Cible = grand public ouvert**, mobile-terrain (canal collecte public, valeur B2B) — 2026-08-05.
- **Capture in-app STRICTE** (`getUserMedia` → canvas, pas de galerie) → fraîcheur (`captured_at`),
  localisation (géoloc live), pas d'EXIF — 2026-08-07.
- **Mail contributeur = OPTIONNEL & NON VÉRIFIÉ** (juste pour être prévenu de la publication). **Plus de
  code**, **plus d'email-bombing**, **zéro friction**. La qualité vient de la **modération**, pas d'une
  barrière ; l'anti-flood vient de **capture live + IP + auto-blacklist** — 2026-08-07.
- **Modération par mail** : photo + détails + **liens d'action signés** (Publier/Rejeter/Blacklister), avec
  **GET de confirmation sans effet + POST** (anti-préchargement) — 2026-08-07.
- **Refus si aucun hotspot < 10 km** ; **contexte page feu = présélection** ; **conflit → géoloc prime**.
- **Placement du bouton** : CTA contextuel visible sur **fiche feu + carte** ; entrée discrète dans le **menu
  hamburger** ailleurs ; proéminence calibrée, pas de masquage passif — 2026-08-07.
- **Fonctionne hors PWA** (navigateur mobile + HTTPS) ; **détecter les navigateurs in-app** (réseaux sociaux)
  où `getUserMedia` est bloqué → message « ouvrir dans Safari/Chrome » — 2026-08-07.
- **Pas d'espace parallèle** : mini-API same-origin (`/api/contrib`) + admin `/admin/contrib` ; un seul site,
  une PWA ; pas de CORS.
- **Contributions dans leur base** ; **socle mono-écrivain préservé** ; **auto-filtre + purge = jobs API** ;
  **widget côté client**, **pas de regen**, **SEO widget d'abord**.
- **GPS = hotspot, jamais l'auteur** ; **preuve de consentement** stockée.
- **Auto-filtre** NudeNet + CLIP zero-shot ONNX, worker à la demande, puis **modération humaine**.
- **Anti-abus** = quota IP + **blacklist IP** (manuelle & auto).
- **Purge rejetées 6 mois** (photo + perso détruites, squelette LCEN) ; **publiées conservées** ; email purgé.
- **Dérogation P1 cloisonnée**.

**`OUVERT` :**
- **Accès API→socle** : lecture WAL directe vs **read-model** exporté par le daemon.
- **Techno mini-API** (FastAPI/Flask…) + intégration reverse proxy + infra mail (réutiliser Spec 09 ?).
- **Hotspot pas encore cluster** au dépôt : accepter + rattacher (défaut) vs refuser.
- **Fraîcheur du hotspot** : n'accepter que près d'un hotspot **récent** (< N h) vs laisser la modération.
- **SEO** : « cuisson » future des photos dans le HTML (indexation, Open Graph).
- **Floutage auto** visages/plaques : non retenu v1 (la modération rejette).
- **Violence/gore** : non couvert par NudeNet → modération humaine, ou API UE (Sightengine) plus tard.
- **Ops avant `activated = true`** : SMTP, volumétrie modération, documents juridiques.

**Hors périmètre — modules futurs (specs séparées) :**
- **Chat** et **Solidarité** : écartés (2026-08-06, classe de risque temps réel/urgence). **Photo d'abord** ;
  solidarité un jour en **post-événement, asynchrone, modérée, barrière anti-urgence** (« pas un service de
  secours — 18/112 ») ; **chat temps réel à questionner**.
