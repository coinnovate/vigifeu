# Vigifeu — Spécification 09 : Bulletins de veille presse

**Version :** 0.1 (2026-08-04 — cadrage initial)
**Références :** Spec 01 (P1 immuabilité, P3 double horodatage, P4 catégories, §3.7 `ingestion_run`,
§3.8 `regen_queue`, §4.1 `fire_event`, §5.4 `fe_commune_rel`), Spec 02 (§2 cycle, §9 monitoring),
Spec 03 (§1 principes de libellé, §3.3 fiche feu), Spec 04 (générateur, `regen_queue`),
Spec 05 (§0 principe de responsabilité **P0**), plan de dev §1.1 (**écrivain SQLite unique**).
**Périmètre :** un enrichissement de la **fiche feu** — un **bulletin de veille presse** quotidien par feu
actif, produit par un appel à l'API tierce `news.co-innovate.eu` (scraping presse + consolidation IA),
stocké de façon **immuable** et affiché en **timeline datée**. Le bulletin est de la **donnée presse
attribuée**, d'une **lignée distincte** de l'observation satellite (vérité terrain du socle).
**Statut :** cadrage. Aucune ligne de code encore. À exécuter en petits pas (§8) après validation.

---

## 0. Discipline P0 — une lignée à ne jamais confondre avec le satellite

Le socle Vigifeu est une **observation instrumentale** (FIRMS/Sentinel) : « un capteur a vu ça, à cette
date ». Un bulletin presse est **autre chose** : une **synthèse de ce que des humains rapportent** dans la
presse, consolidée par IA. Les deux ne se mélangent pas et ne se réconcilient pas :

- le bulletin est **attribué** (« selon la presse, au {date}, {N} sources concordantes ») et **daté**,
  jamais présenté comme un constat Vigifeu ;
- il peut **contredire** le satellite (presse « maîtrisé » alors que le satellite détecte encore) : on
  affiche les **deux lignées** côte à côte avec leurs horodatages, on n'arbitre pas (Spec 03 §1) ;
- catégorie de donnée (Spec 01 P4) : **`declaree`** (source tierce), jamais `mesuree`. Le suffixe/segment
  d'affichage doit rendre l'origine presse **non ambiguë** ;
- **chiffres sensibles** (blessés, morts) : rendus **strictement** « selon la presse ({N} sources) au
  {date} », jamais affirmés. Un champ vide reste vide (l'API renvoie `inconnu` honnêtement, §2.3).

Conséquence assumée : la **majorité des feux** (petits feux de végétation) n'ont **aucune couverture
presse** → bulletin vide, honnête. La valeur se concentre sur les **feux notables**.

---

## 1. La source — API `news.co-innovate.eu`

Service tiers (co-innovate.eu). Le contrat est décrit dans le guide d'intégration fourni ; rappel des
points qui **décident** de notre intégration.

### 1.1 Ce qu'on envoie / ce qu'on reçoit

- **Entrée** (`POST /recherches`, JSON) : `mots_cles` (texte), `date_jour` (`JJ/MM/AAAA`, filtre sur la
  **date de publication** des articles), `indicateurs` (liste d'indicateurs typés), + options
  (`nb_articles`, `fenetre_jours`, `min_sources`, `langue`/`pays`).
- **Sortie** (résultat) : par indicateur `{valeur, statut, sources[]}` avec `statut ∈
  {confirmé, environ, inconnu}` ; un champ **`resume`** de 2–4 phrases construit **uniquement** à partir
  des valeurs **confirmées** ; méta `articles_analyses/valides/rejetes`, `fournisseurs_ia`, `erreurs`.
- **Le bulletin = le champ `resume`.** Les indicateurs structurés sont stockés aussi (§3), affichés en
  second niveau, mais le corps lisible du bulletin est `resume`.

### 1.2 Contraintes opérationnelles (elles cadrent le job §5)

| Contrainte | Conséquence pour nous |
|---|---|
| Traitement **20–60 s** (scraping + IA), mode **asynchrone** (POST → `202` + `id_tache`, puis `GET /recherches/{id_tache}` jusqu'à `termine`/`erreur`) | polling toutes ~3 s ; **timeout dur** par feu |
| **Pas d'authentification** (qui connaît l'URL consomme le quota) | appel **côté serveur uniquement** (le daemon), jamais depuis le front |
| **CORS non autorisé** | idem — appel backend, pas de `fetch()` navigateur |
| Offre **gratuite Mistral**, `429` possibles (repli Groq annoncé) | **backoff** sur 429, dégradé honnête, jamais bloquant |
| Contenu = **texte IA d'un tiers** | traité comme **non fiable** : échappé au rendu (autoescape Jinja), longueur plafonnée, jamais injecté brut |

**Garde-fou natif appréciable :** `resume` n'utilise que les valeurs confirmées par **≥2 sources**
(`min_sources`), pas d'invention. C'est le principal argument pour intégrer cette source plutôt qu'un LLM brut.

**Pré-requis ops :** l'hôte `https://news.co-innovate.eu` doit être **joignable depuis le VPS** (à vérifier
avant de coder). URL de base **paramétrée** (§7), jamais en dur.

---

## 2. Modèle de données — table `bulletin` (immuable)

Nouvelle table d'**observation** (P1 : on insère, on ne réécrit jamais, on ne supprime jamais).

| Champ | Type | Description |
|---|---|---|
| `id` | INTEGER PK | interne |
| `fire_event_id` | FK → `fire_event` | feu concerné |
| `date_bulletin` | TEXT (`YYYY-MM-DD`) | jour de veille (date Europe/Paris, cf. §5) |
| `mots_cles` | TEXT | mot-clé effectivement envoyé (traçabilité, §4) |
| `resume` | TEXT | le corps du bulletin (`resume` de l'API) — **peut être vide** |
| `indicateurs_json` | TEXT | liste `{indicateur, valeur, statut, sources}` (audit + 2ᵉ niveau) |
| `sources_json` | TEXT | URLs distinctes citées (dédupliquées des indicateurs) |
| `articles_valides` | INTEGER | nb d'articles retenus (contexte de fiabilité) |
| `fournisseurs_ia` | TEXT | `fournisseurs_ia` de l'API (traçabilité) |
| `provider` | TEXT | `co-innovate` (le service), pour un futur changement de source transparent |
| `acq_at` | TEXT UTC | horodatage du phénomène = borne de la veille (P3-a) |
| `ingested_at` | TEXT UTC | quand nous l'avons su (P3-b) — **jamais réécrit** |

**Idempotence :** au plus **un bulletin terminé par `(fire_event_id, date_bulletin)`**. Un rejeu du job le
même jour qui trouve déjà un bulletin pour ce couple **ne réinsère pas** (P1 : pas d'écrasement). Les
**tentatives et erreurs** (429, timeout, feu sans presse) ne sont **pas** des lignes `bulletin` : elles vont
dans `ingestion_run` (Spec 01 §3.7, la boîte noire) — quotas et lenteurs deviennent observables comme pour
FIRMS. *(Contrainte SQL : index unique partiel `(fire_event_id, date_bulletin)`.)*

**Bulletin vide (`resume` vide ET 0 indicateur confirmé) :** décision §9 (`OUVERT`) — par défaut **on ne
crée pas** de ligne (sinon la timeline se remplit de « rien » quotidiens) ; l'absence est consignée dans
`ingestion_run`. La fiche peut alors afficher « pas de couverture presse à ce jour » sans entrée datée.

**Migration** : `migrations/006_bulletins.sql` (numéro suivant ; 001–005 pris). PostGIS-ready (que du
scalaire/texte, aucune géométrie).

---

## 3. Construction du mot-clé — commune principale

Décision (2026-08-04) : `mots_cles = "incendie " + nom_commune` (repli `"feu " + nom_commune` — cf. §9).

**Commune principale** d'un feu, dans l'ordre :

1. la commune `rel_type = emprise_dans_commune` de **plus grande intersection** avec l'emprise (celle qui
   porte le plus de surface détectée) — même source que le titre de fiche / le slug du `public_id` ;
2. à défaut d'emprise (feu tout juste détecté, seulement des relations de proximité), la commune
   `a_moins_de_5km` la **plus proche** (`distance_km` min) ;
3. à défaut, **pas de mot-clé fiable → pas d'appel** (consigné, pas d'erreur).

Le `nom` vient de la table `commune` (Spec 01 §5.2). On envoie un **seul** mot-clé ; l'indicateur de sortie
`communes concernées` couvre les feux multi-communes sans multiplier les appels.

⚠️ **Risque de faux appariement** (homonymes, autre feu, autre année) : borné par `date_jour` (publication
du jour) + la règle **≥2 sources** de l'API, **jamais éliminé**. D'où l'affichage **attribué + sourcé** (§6)
et la réserve P0 (§0). Amélioration ouverte : mot-clé éditorial optionnel `nom_presse` par feu (§9).

---

## 4. Le job `bulletins-generer` — **dans le daemon**, non bloquant

**Où.** Un `scheduler.add_job(..., "cron", ...)` **dans `scheduler.py`**, comme `commune_context`. **Pas de
timer systemd séparé** : le daemon est l'**écrivain SQLite unique** (plan §1.1, worker unique). Un cron
externe casserait cet invariant.

**Quand.** ~**15h00 Europe/Paris**. Le scheduler tourne en UTC, mais APScheduler accepte un `timezone` par
job → `timezone="Europe/Paris"` sur ce job (gère l'heure d'été sans param DST). `date_jour` envoyé à l'API =
**date Europe/Paris** du jour (la presse publie en heure locale).

**Sélection.** Feux **actifs uniquement** (décision 2026-08-04) :
`lifecycle='actif' AND qualification='vegetation_confirme'` — même filtre que `weather_obs`. *(Le bilan de
clôture d'un feu passé `plus_detecte` est une extension future, §9 ; le modèle immuable l'accueille sans
casse.)*

**Comment (non bloquant).** Worker unique + 20–60 s par appel ⇒ séquentiel, N feux bloqueraient le cycle
`fetch_firms` (15 min). Donc :

1. **Fan-out réseau concurrent** (pool borné `concurrence`, §7) : POST + polling de chaque feu en threads,
   **hors** écriture DB. Mur de temps ≈ feu le plus lent, pas la somme.
2. **Écritures DB sérielles** dans le worker du scheduler après collecte (insert `bulletin` + `ingestion_run`)
   → **un seul écrivain** préservé.
3. **Timeout dur** par feu + **plafond global** de temps de job (§7) ; dépassement = dégradé consigné, on
   n'attend pas indéfiniment.
4. **Backoff** sur `429` (repli borné), **retries** limités (comme les fetchers, `max_retries`/waits en §7).
5. **`max_feux_par_jour`** (§7) : garde-fou quota ; au-delà, priorité aux plus gros feux (FRP/emprise
   décroissante) et le reste **consigné comme non traité** (jamais un cap silencieux — Spec 02 §9).

**Après.** Pour chaque feu ayant un **nouveau** bulletin : `enqueue(conn, "feu", str(fire_id), ...)` dans la
`regen_queue` (Spec 01 §3.8) puis `run_regen("bulletins")` — la fiche se régénère dans la foulée, pattern
identique à `weather_obs`/`contexte`. Ping healthcheck dédié (`HEALTHCHECK_BULLETINS_URL`, §7, secret d'env).

---

## 5. Affichage sur la fiche feu (Spec 03 §3.3)

Section **« Bulletins de veille presse »**, distincte visuellement du reste (lignée `declaree`) :

- **Timeline datée**, plus récent en tête : chaque entrée = `resume` (échappé, plafonné) + **date du
  bulletin** + **liens sources** + badge **« Veille presse — à vérifier »**.
- **Second niveau** (repliable) : les indicateurs `confirmé`/`environ` avec leur valeur (les `inconnu` non
  affichés — cf. « champs vides = honnêtes » du guide).
- **Réserve** (encadré + page méthodologie) : « Synthèse d'articles de presse consolidée automatiquement au
  {date}. Ne reflète pas une observation satellite. La situation a pu évoluer. »
- **Repli** au-delà de N entrées (`<details>`), même parti que la chronologie des passages (Spec 04,
  `chrono_repli_*`).
- Aucune chaîne métier libre dans `generate/feu.py` : les libellés passent par `lexique/fr.py` (Spec 03 §2).

---

## 6. Robustesse & sécurité (récap opposable)

- **Backend only** (pas d'auth, pas de CORS) : l'appel vit dans le daemon, jamais dans le front.
- **Contenu tiers non fiable** : `resume`/indicateurs **échappés** (autoescape Jinja déjà en place),
  longueur plafonnée, URLs sources **validées** (`https?://` seulement) avant rendu.
- **Jamais bloquant** : timeouts, plafond de job, fan-out concurrent + écriture sérielle (§4).
- **Idempotent** : un bulletin par feu/jour, rejeu sûr (§2).
- **Observable** : chaque tentative (succès/vide/429/timeout) dans `ingestion_run` ; `gap`/healthcheck.
- **Immuable** : aucune réécriture, `ingested_at` figé (mesure de latence, P3).

---

## 7. Paramètres (`config/params.toml`, section `[bulletins]`)

Tout ce qui décide vit ici (règle projet), jamais en dur :

```toml
[bulletins]
activated = false                       # master off tant que l'API n'est pas validée depuis le VPS
base_url = "https://news.co-innovate.eu"
heure_locale = 15                       # heure Europe/Paris de déclenchement (cron tz=Europe/Paris)
timezone = "Europe/Paris"               # fuseau du job ET de date_jour envoyée à l'API
nb_articles = 4                         # cible d'articles exploitables
min_sources = 2                         # concordance minimale (garde-fou anti-invention)
fenetre_jours = 1                       # tolérance : jours AVANT date_jour aussi inclus
langue = "fr"
pays = "FR"
mot_cle_prefixe = "incendie"            # repli "feu" (§9)
max_feux_par_jour = 50                  # garde-fou quota ; au-delà = priorité FRP, reste consigné
concurrence = 4                         # fan-out réseau (threads), écriture DB reste sérielle
timeout_feu_s = 120                     # timeout dur par feu (polling)
timeout_job_s = 600                     # plafond global du job (non bloquant)
poll_intervalle_s = 3                   # cadence d'interrogation GET /recherches/{id}
max_retries = 2
retry_wait_min_s = 5
retry_wait_max_s = 60
provider = "co-innovate"
# Jeu d'indicateurs « incendie de forêt » (recommandé par le guide). Grandeurs croissantes → agregation="max".
indicateurs = [
  { nom = "surface brûlée",                    type = "chiffre", unite = "ha",           agregation = "max" },
  { nom = "nombre de pompiers mobilisés",      type = "chiffre", unite = "pompiers",     agregation = "max" },
  { nom = "avions bombardiers d'eau",          type = "chiffre", unite = "avions",       agregation = "max" },
  { nom = "hélicoptères bombardiers d'eau",    type = "chiffre", unite = "hélicoptères", agregation = "max" },
  { nom = "personnes évacuées",                type = "chiffre", unite = "personnes",    agregation = "max" },
  { nom = "habitations menacées ou détruites", type = "chiffre", unite = "bâtiments",    agregation = "max" },
  { nom = "nombre de blessés",                 type = "chiffre", unite = "personnes",    agregation = "max" },
  { nom = "nombre de morts",                   type = "chiffre", unite = "personnes",    agregation = "max" },
  { nom = "communes concernées",               type = "texte" },
  { nom = "département",                        type = "texte" },
  { nom = "jour de début",                     type = "date" },
  { nom = "heure de début",                    type = "heure" },
  { nom = "origine", type = "options", options = ["accidentelle", "criminelle", "foudre", "inconnue"] },
  { nom = "statut du feu", type = "options", options = ["hors de contrôle", "fixé", "maîtrisé", "éteint"] },
]
```

Secret d'env (jamais dans le dépôt) : `HEALTHCHECK_BULLETINS_URL` (dead-man switch, comme les autres jobs).

---

## 8. Étapes de développement (petits pas, tests au fil, commits FR)

1. **Migration** — `migrations/006_bulletins.sql` : table `bulletin` + index unique partiel
   `(fire_event_id, date_bulletin)`. `pytest` vert.
2. **Client API** — `src/vigifeu/ingest/bulletins.py` : `POST /recherches` async + polling + timeouts +
   backoff 429, entièrement piloté par `[bulletins]`. **Fixture** de réponse figée (basée sur l'exemple
   Saumos du guide) → test unitaire du parsing (résumé + indicateurs + sources), **sans réseau**.
3. **Construction du mot-clé** — helper « commune principale d'un feu » (§3) + test (emprise > proximité >
   aucun).
4. **Orchestration** — fonction `generer_bulletins(conn, config, clock)` : sélection feux actifs, fan-out
   concurrent, écriture sérielle, idempotence, `ingestion_run`. Tests : idempotence (rejeu = no-op), feu
   sans presse (aucune ligne + trace `ingestion_run`), 429 (consigné, non bloquant).
5. **Câblage daemon** — `add_job` cron `tz=Europe/Paris` `heure_locale` dans `scheduler.py` + enqueue regen
   + healthcheck.
6. **Fiche** — libellés `lexique/fr.py`, section timeline dans `generate/feu.py` + `templates/feu.html.j2`,
   réserve méthodologie. **Golden régénéré** (fixture Saumos) : la fiche montre un bulletin daté + sources.

**Jalon J-BULLETIN :** sur la fixture Saumos, un rejeu produit un bulletin daté (`resume` non vide, sources
cliquables) affiché sur la fiche, idempotent, `ingestion_run` renseigné, les 8 tests historiques + nouveaux
verts.

---

## 9. Décisions & questions ouvertes

**Décidé (2026-08-04) :**
- **Mot-clé** = `"incendie " + commune principale` (repli `"feu "`).
- **Périmètre** = feux **actifs** seulement.
- **Bulletin = champ `resume`** ; indicateurs stockés en second niveau.
- **Job dans le daemon** (écrivain unique), **non bloquant** (fan-out concurrent + écriture sérielle).
- **Catégorie `declaree`**, lignée presse **distincte** du satellite (P0).

**`OUVERT` :**
- **Bulletin vide** : ne pas créer de ligne (défaut proposé) vs marqueur « rien ce jour » daté. À trancher
  au premier rejeu réel.
- **Fréquence** : un bulletin/jour fixe vs déclenchement supplémentaire sur évènement (nouveau gros feu hors
  15h). Défaut : 15h quotidien seul.
- **Seuil de notabilité** : n'appeler qu'au-dessus d'un FRP/emprise pour économiser le quota et éviter les
  résumés vides (envisagé, non retenu en v1 — `max_feux_par_jour` suffit au départ).
- **`nom_presse` éditorial** : champ optionnel par feu (« Feu de Saumos ») pour de meilleurs appariements,
  fallback commune. Amélioration qualité, demande une saisie.
- **Bilan de clôture** : bulletin final quand un feu passe `plus_detecte` (bilan presse consolidé). Extension
  future, compatible modèle immuable.
- **Joignabilité VPS → `news.co-innovate.eu`** et robustesse réelle de l'API (SLA, quota) : à **vérifier
  avant** de passer `activated = true`.

---

## 10. Cadre juridique (règles dures, opposables)

**Contexte de propriété (2026-08-04) :** le service source `news.co-innovate.eu` **appartient à l'éditeur de
Sentifeu** (même personne). Conséquences :

- la **licence de réutilisation** de la sortie est maîtrisée (auto-accordée) — pas de risque « API tierce
  sans conditions » ;
- en contrepartie, la **légalité du scraping de presse en amont** relève **entièrement** de l'éditeur, à
  sécuriser **côté API** (hors de cette spec). Le vrai sujet y est **contractuel/technique**, pas du droit
  de PI : **CGU** des sites (extraction automatisée parfois interdite) et **`robots.txt`**. Le **droit *sui
  generis* des bases de données** (L.341-1 CPI) est **marginal ici** — on lit **un** article et on n'en tire
  que des faits (part **non substantielle**), et la jurisprudence CJUE (British Horseracing, Football Dataco)
  n'accorde ce droit qu'à un investissement dans l'**obtention/vérification** des données, pas leur
  **création** (or un journal crée son contenu). Ne subsiste qu'un angle « extraction **répétée et
  systématique** de parties non substantielles », faible vu le volume et le fait qu'on n'extrait que des faits.

**Côté affichage Sentifeu, ces règles gardent le bulletin hors des droits d'autrui.** Toute évolution qui
les enfreint (afficher un extrait, un nom, une photo) doit **repasser par cette section**.

| Règle | Fondement | Mise en œuvre |
|---|---|---|
| **Faits seulement**, jamais reproduction d'article | Faits d'actualité non protégeables ; considérant 57 dir. UE 2019/790 | le `resume` reformule des valeurs **confirmées** ; contrôle **anti-verbatim** sur vraie sortie + plafond de longueur |
| **Liens, pas extraits** | Droit voisin éditeurs (art. L.218-1 CPI) : hyperliens + mots isolés **exclus** ; extraits/snippets = déclencheur | on lie vers les sources **par hôte** ; **aucun snippet** d'article recopié |
| **Comptes, jamais de noms** | RGPD (données perso, parfois sensibles : santé, pénal) sur site public indexé | indicateurs **numériques** (blessés, morts, évacués) ; **aucun nom de personne** stocké ni affiché |
| **Aucune photo de presse** | Droit d'auteur / droits des agences | le rendu n'affiche **que du texte** + liens ; jamais d'image tierce |
| **Attribution + date + « à vérifier »** | Responsabilité éditoriale (diffamation, fausse info) | Sentifeu = **rapporteur sourcé**, pas auteur ; réduit sans annuler → l'**appariement correct** (§3) est critique |

**Revue juridique PI/presse recommandée avant le passage commercial public** (B2B payant, Phase 2) — pas
requise pour un prototype interne, mais le risque croît avec la visibilité et le chiffre d'affaires.
