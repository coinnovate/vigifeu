# Vigifeu — Spécification 04 : Générateur statique & SEO/GEO

**Version :** 0.4
**Références :** cadrage v0.3 (§8.5, §8.6, §15bis), Spec 01 (§8), Spec 02 (§8), Spec 03 (lexique et structures de pages)
**Journal :** 0.4 — ajout §11 (PWA « installable safe »).
**Périmètre :** production des pages publiques et stratégie de visibilité (moteurs de recherche classiques et moteurs génératifs / assistants IA). Dernier module du socle pré-SaaS.

---

## 1. Principes

**P1 — Une page est une fonction pure des données.** `page = gabarit(données, config_lexique)`. Aucun état caché, aucune rédaction manuelle dans les pages générées : deux exécutions sur les mêmes données produisent le même HTML. C'est ce qui rend la régénération sélective sûre et le rejeu (Spec 02 P2) applicable au site entier.

**P2 — Génération événementielle et sélective.** Le générateur consomme la liste de pages impactées émise par le pipeline (Spec 02 §8). Il ne régénère jamais « tout le site » en fonctionnement nominal.

**P3 — Le contenu est complet sans JavaScript.** Toute l'information (synthèse, communes, chronologie, indices) est dans le HTML. Le JS n'apporte que du confort : carte interactive, durées relatives (« il y a 3 h ») calculées côté client, repli des données brutes. Raison double : robustesse (un préfet sur un téléphone en zone blanche partielle) et lisibilité machine (crawlers classiques et IA lisent le HTML, pas toujours le JS).

**P4 — SEO/GEO est une exigence de premier rang** (cadrage Spec 01 §8), pas une finition. Chaque décision de gabarit se pose la question : « cette information est-elle lisible, datée, sourcée et citable par une machine ? »

**P5 — Écriture atomique.** Une page est écrite dans un fichier temporaire puis renommée ; le site n'expose jamais une page à moitié générée. La régénération d'un lot (passage satellite) est publiée d'un bloc.

---

## 2. Architecture du générateur

* **Entrée** : liste de pages impactées (type + identifiant : `carte`, `feu:2026-saumos`, `commune:33333`), lue depuis la file `regen_queue` (Spec 01 §3.8) alimentée par le pipeline.
* **Moteur de gabarits** : Jinja2 (ou équivalent). Les gabarits assemblent exclusivement les formulations du **lexique contractuel** (Spec 03 §2), implémenté comme une bibliothèque de fonctions versionnée — `libelle_cycle_de_vie(fire_event)`, `phrase_progression(version_a, version_b)` — jamais de chaîne libre dans un gabarit.
* **Sortie** : arborescence de fichiers statiques (`/feux/…/index.html`, `/communes/…/index.html`, assets), servie par Nginx ou un CDN.
* **Cartes** : MapLibre GL (ou Leaflet) chargé côté client sur un GeoJSON pré-généré par page ; fond de carte sobre ; la légende contractuelle (Spec 03 §2.3) fait partie du GeoJSON/gabarit, pas du JS.
* **Composants partagés** : badge cycle de vie, badge confiance, bloc météo horodaté, bloc latence (Spec 03 §2.6), **bloc attributions sources (Spec 03 §2.7 — NASA FIRMS + disclaimer, IGN + millésime, Open-Meteo CC BY, BDIFF/Prométhée, EUMETSAT en phase 2)**, encadré limites, fil d'ariane. Un composant = un gabarit unique réutilisé — cohérence garantie entre fiches.
* **i18n prête** : les gabarits et le lexique sont structurés par langue dès le départ (fr seul en v1) — l'extension Espagne (cadrage §3) ne devra pas casser l'assembleur.

---

## 3. Régénération : cadence et lots

| Déclencheur | Pages régénérées | Délai cible |
|---|---|---|
| Cycle avec nouveauté (passage VIIRS, MTG en phase 2) | carte nationale, fiches des feux modifiés, fiches des communes concernées | < 2 min après fin d'ingestion |
| Nouvelle `weather_obs` sur feu actif | fiches feux concernées ; fiches communes dont une relation `direction_vent` s'ouvre ou se ferme (Spec 02 §8) | < 2 min |
| Quotidien matin (après `fetch_drought` / VigiEau) | fiches communes du périmètre — actives **et** « rien à signaler » (bloc contexte du jour) + fiches feux | < 30 min |
| Nocturne | sitemaps, maintenance, vérifications d'intégrité | — |
| Archivage d'un feu | fiche feu en mode archive (dernière génération, depuis l'archive Parquet) | — |

Si un CDN est utilisé : invalidation ciblée des URLs régénérées, jamais de purge globale. Les en-têtes de cache distinguent pages chaudes (carte, feux actifs : `max-age` court) et pages froides (archives, communes calmes : long, revalidation).

**Résilience aux pics** : le jour d'un grand feu, le trafic est maximal exactement quand les sources amont ralentissent (constaté, cadrage §5.1). Le statique découple les deux : le site tient la charge d'un pic médiatique sans toucher à la base — c'est un argument d'architecture, pas un détail d'hébergement.

---

## 4. URLs, routage, redirections

* `/` carte nationale ; `/feux/{annee}-{slug}/` ; `/communes/{code-insee}-{slug}/` ; `/departements/{num}-{slug}/` (pages de liste, cf. §5) ; `/saison/{annee}/` ; `/methodologie/` ; `/mentions-legales/`, `/cgu/`. (Les pages départements étendent l'arborescence du cadrage §8.6 — structure de crawl et de navigation, pas un nouveau type de contenu.)
* **Canonical** sur chaque page ; slash final normalisé ; 404 propre avec lien carte et recherche de commune.
* **Redirections permanentes (301)** générées avec le site : feux fusionnés → feu absorbant avec ancre (Spec 03 §5) ; communes fusionnées (`commune_succession`, Spec 01 §5.2) → commune nouvelle. Une URL publiée ne meurt jamais (Spec 01 P6).
* Si le slug d'un feu doit changer (rare : renommage du lieu principal), l'ancienne URL redirige — le `public_id` en base ne change pas.

---

## 5. SEO technique

* **Title et meta description générés** depuis les données, gabarits dédiés :
  * fiche feu active : `Feu de {lieu} ({dept}) — suivi satellite, communes concernées | Vigifeu` ; description = 1re phrase de la synthèse factuelle (Spec 03 §3.2) ;
  * fiche feu archivée : `Feu de {lieu}, {mois annee} — {N} ha estimés, relecture de la propagation | Vigifeu` ;
  * fiche commune : `Incendies à {commune} ({dept}) — situation, historique, exposition | Vigifeu` (mention « depuis 1973 » réservée aux communes couvertes par Prométhée, « depuis 2006 » sinon — Spec 01 §5.3).
* **Sitemaps segmentés** : `sitemap-feux.xml`, `sitemap-communes.xml`, `sitemap-pages.xml`, index à la racine. `lastmod` = horodatage de la **donnée** la plus récente de la page (P5 de Spec 03, appliqué jusqu'au sitemap).
* **Maillage interne** : la boucle feu ↔ communes (cadrage §8.6) est le maillage principal ; s'y ajoutent communes limitrophes, feux de la même commune (historique), fil d'ariane (`Accueil → Gironde → Le Porge`). Départements comme pages de liste légères (`/departements/33-gironde/` : communes du périmètre + feux de l'année) pour donner une structure de crawl.
* **Open Graph / cartes de partage** : image générée par page (carte simplifiée du feu ou de la commune, rendu au moment de la génération) — un lien Vigifeu partagé pendant un feu doit montrer la carte, c'est le premier vecteur de notoriété en saison.
* **Performance** : statique + HTML complet sans JS (P3) ⇒ LCP et CLS excellents par construction. Budget : < 100 ko HTML+CSS par fiche hors carte ; la carte se charge après le contenu.
* **Indexation progressive** : le périmètre « rien à signaler » (communes BDIFF + concernées, cadrage §8.6) définit l'ordre de mise en ligne ; pas de publication massive de 35 000 pages d'un coup (risque de crawl budget et de contenu perçu comme mince) — montée par vagues, en commençant par les communes à historique riche.

---

## 6. GEO — visibilité dans les moteurs génératifs

Les assistants IA citent les sources qui leur donnent des **faits datés, sourcés, stables et attribuables**. Tout le socle y contribue déjà ; cette section le rend explicite.

* **Le paragraphe citable** : la synthèse factuelle (Spec 03 §3.2) est écrite pour être reprise telle quelle — phrases autonomes, datées, avec unités et sources (« Premier hotspot VIIRS le 22/07/2026 à 12:32 UTC, source NASA FIRMS »). C'est l'unité de citation des moteurs génératifs.
* **JSON-LD (schema.org)** sur chaque page, généré depuis le modèle (Spec 01 §8) :
  * feu : `Event` (+ `Place` pour la zone), dates de première/dernière détection, `about` → communes ;
  * commune : `Place` / `AdministrativeArea` avec `geo`, population, liens feux ;
  * éditeur : `Organization` (nom, logo, mentions) sur toutes les pages ;
  * méthodologie : `FAQPage` (définitions des libellés : « que signifie “plus détecté” ? ») — format directement exploitable par les moteurs classiques et génératifs.
* **Crawlers IA autorisés** : robots.txt ouvre explicitement GPTBot, ClaudeBot, PerplexityBot, Google-Extended, etc. La visibilité dans les assistants est un canal d'acquisition, pas une fuite — le socle public est fait pour être cité (la valeur payante est ailleurs : sites déclarés, notifications, API).
* **`llms.txt`** à la racine : description du site, de la sémantique des libellés, des sources et de la licence de citation souhaitée (« citer avec la date d'observation et le lien »). Coût nul, adoption croissante.
* **Flux Atom/RSS** : `feux.xml` (nouveaux feux publiés et changements majeurs) — consommé par les rédactions et par des agrégateurs, multiplie les chemins de découverte.
* **Page méthodologie comme signal de fiabilité** (cadrage §15bis) : sources primaires nommées, latence chiffrée, limites explicites — les critères mêmes qu'un moteur génératif applique pour choisir qui citer.

---

## 7. Mesure de visibilité

* **Search Console + sitemaps** : couverture d'indexation par segment (feux / communes), requêtes gagnées — cibles types : « historique incendies {commune} », « feu {lieu} {annee} », « incendie {département} aujourd'hui ».
* **Logs serveur** : suivi des passages de crawlers classiques et IA (user-agents) ; un tableau de bord simple (DuckDB sur logs) suffit en v1.
* **Suivi de citation générative** : échantillon mensuel de questions posées aux principaux assistants (« que s'est-il passé lors du feu de Saumos en 2026 ? », « ma commune X est-elle exposée aux feux ? ») avec relevé des sources citées. Manuel en v1, c'est un indicateur produit, pas une vanité.
* **Analytics respectueux** (sans cookie, type Plausible/Matomo) : pages vues par type, pics en saison, provenance — cohérent avec le positionnement RGPD (cadrage §15).

---

## 8. Accessibilité et lecture terrain

* La synthèse factuelle **est** l'alternative textuelle de la carte (pas d'information portée uniquement par la couleur ou la géométrie) ; libellés de cellules disponibles en texte (Spec 03 §2.1).
* Contrastes AA, tailles lisibles en mobilité, pages consultables d'une main sur mobile — l'utilisateur type en saison est dehors, en déplacement, sur un réseau moyen. Le budget perf (§5) est aussi une exigence terrain.
* Palette de la carte lisible par les daltoniens (le rouge/gris du cycle de vie doublé par la forme ou l'intensité, pas seulement la teinte).

---

## 9. Tests et garde-fous

1. **Lint du lexique (test automatique clé)** : à chaque build, grep des termes interdits (« éteint », « menacé », « propagation estimée », « sera touché », « maîtrisé » hors citation `declaree`…) sur l'intégralité du HTML généré. Un terme interdit = build en échec. C'est le §4.1 du cadrage transformé en test CI.
2. **Fixture Saumos** : la fiche du feu de référence, générée depuis l'archive, comparée à une version approuvée (golden file) — toute évolution de gabarit se relit sur ce diff.
3. **Validation JSON-LD** (schéma) et HTML (W3C) sur l'échantillon de pages de chaque build.
4. **Budget perf** vérifié en CI (taille HTML, absence de JS bloquant).
5. **Aucun horodatage de génération dans le HTML** (grep inverse) — seule l'heure de la donnée apparaît (Spec 03 P5).
6. **Garde-fou PWA (§11)** : test automatique vérifiant que le service worker ne précache **ni** page de feu (`/feux/…`) **ni** GeoJSON — le cache ne doit jamais pouvoir servir une détection périmée.

---

## 10. Points à trancher

1. Génération des images de partage (Open Graph) : rendu serveur d'une carte statique (coût, dépendance) vs image générique par département en v1.
2. Choix CDN vs Nginx seul au lancement (volumétrie faible, mais pics médiatiques violents — trancher sur le coût).
3. Pages départements : simples listes (retenu par défaut) ou fiches enrichies (Météo des forêts du jour, historique départemental).
4. Flux Atom : périmètre des « changements majeurs » d'un feu justifiant une entrée (nouvelle commune concernée ? progression > N km ?).
5. Politique exacte de `llms.txt` et conditions de citation — à aligner avec les CGU (module juridique).

---

## 11. Progressive Web App (PWA)

Le site est **installable** sur l'écran d'accueil (mobile et desktop) via un Web App Manifest et un service worker. Niveau retenu : **« installable safe »** — l'app et le confort d'installation, sans jamais introduire le risque de servir une donnée périmée. Fonctionnalité facultative et réversible : elle tient dans une section `[pwa]` de `config/params.toml` ; la retirer suffit à revenir à un statique Nginx ordinaire.

**11.1 Ce qui est émis.** Trois artefacts « site-level », écrits en fin de build (`generate/pwa.py`, appelé par `finalize_site`, au même titre que sitemaps/robots) :

* `manifest.webmanifest` (racine) — nom, `short_name`, description, `theme_color`/`background_color`, `display: standalone`, `start_url: /`, `scope: /`, icônes 192/512 (déjà présentes, `purpose: any` — nos icônes ne sont pas dessinées *maskable*). Tous les champs viennent de la config `[pwa]`, aucune constante en dur.
* `sw.js` (**racine**, jamais sous `/static/` : la portée d'un service worker ne peut pas dépasser son répertoire d'origine ; il doit couvrir `/`).
* `offline.html` (racine) — page de repli servie hors ligne, aux couleurs du site.

Le gabarit de base pose alors sur **toutes** les pages : `<link rel="manifest">`, `<meta name="theme-color">` et l'enregistrement du service worker (injectés via le global Jinja `pwa`, comme `analytics` — pas dans chaque contexte de page). Le contenu reste complet sans JS (P3) : l'enregistrement du SW est une amélioration progressive.

**11.2 Stratégie du service worker (le cœur du « safe »).** La contrainte est celle du produit : les détections évoluent en continu (§9.5, Spec 03 P5) ; un cache agressif servirait des feux périmés — inacceptable. La stratégie est donc **asymétrique** :

| Ressource | Stratégie | Raison |
|---|---|---|
| Shell de présentation (`/static/` : CSS, JS carte, icônes) + `offline.html` + manifest | **stale-while-revalidate**, précaché à l'installation | aucune donnée de feu ; gain de perf terrain |
| Pages HTML (carte, fiches feu/commune) | **réseau d'abord**, repli `offline.html` si hors ligne — **jamais mises en cache** | une fiche périmée ne doit jamais être servie |
| GeoJSON, flux Atom, `carte-config.js` | **réseau direct**, non interceptés | données chaudes ; fraîcheur (P0) |
| Origines tierces (tuiles MapTiler, imagerie Sentinel Hub) | non interceptées | hors périmètre du SW |

`maplibre-gl.js` (~1 Mo) est volontairement **hors précache** (trop lourd pour l'installation) : il est mis en cache opportunistement par la règle *stale-while-revalidate* de `/static/`.

**11.3 Versionnement du cache.** Le nom du cache est une **empreinte du contenu** des assets shell (`sentifeu-shell-<hash>`). Il se renomme dès qu'un CSS/JS change ; l'ancien cache est purgé à l'activation. Pas de version à incrémenter à la main, cohérent avec P1 (le site est une fonction pure des données + des assets).

**11.4 Service (Nginx).** `sw.js` est servi en `Cache-Control: no-cache` (le navigateur revalide à chaque visite, sinon une version périmée du SW peut persister ~24 h) ; le manifest reçoit son type MIME `application/manifest+json` (absent des `mime.types` par défaut). Réglages présents dans les deux vhosts (`deploy/nginx-sentifeu.conf` public et `-beta.conf` privé).

**11.5 Réversibilité.** Retirer la section `[pwa]` cesse d'émettre les trois artefacts et les balises. Un SW déjà installé chez un visiteur se désinstalle de lui-même à la visite suivante, faute de trouver `/sw.js` (404) — aucune manœuvre côté client.

**11.6 Évolution possible (hors périmètre v1).** Un cran « offline complet » ciblé sur les **fiches de feux actifs** (utile en zone mal couverte) reste ouvert, à condition de gérer explicitement la péremption et d'afficher l'âge des données. Non retenu ici : le positionnement veille quasi temps réel fait primer la fraîcheur sur la disponibilité hors ligne.

---

*Le socle documentaire pré-SaaS est complet : cadrage v0.3 + Specs 01 (modèle), 02 (pipeline), 03 (fiches), 04 (générateur & SEO/GEO). Prochaine étape naturelle : développement itératif en commençant par Spec 01 + 02 (ingestion et base), avec le rejeu Saumos comme premier jalon de validation.*
