# Vigifeu — Spécification 03 : Fiches feu et commune

**Version :** 0.4
**Références :** cadrage v0.3 (§4.1, §7ter, §8), Spec 01 (modèle), Spec 02 (pipeline)
**Périmètre :** contenu et libellés des deux pages centrales du site public. La mise en œuvre technique (générateur, gabarits, SEO/GEO) relève de la Spec 04.

---

## 1. Principes d'affichage

**P1 — Deux niveaux de lecture, dans cet ordre.** Premier niveau : la traduction métier, en phrases françaises complètes, générées mécaniquement depuis les données (« Le front a progressé de 5,5 km vers le nord en 25 h »). Second niveau, replié ou en retrait : les données brutes (FRP, coordonnées, indices) pour les experts. Ne jamais inverser — c'est l'anti-modèle du concurrent qui affiche « DC : 456 » en premier.

**P2 — Chaque énoncé est un fait daté et sourcé.** Toute phrase du premier niveau doit être dérivable d'une requête sur le modèle de données, avec sa source et son horodatage affichables. Si une phrase ne peut pas citer sa donnée, elle n'a pas le droit d'exister.

**P3 — Le lexique est contractuel.** Les formulations de la section 2 sont les seules autorisées ; le générateur est un assembleur de gabarits, pas un rédacteur libre. Toute nouvelle formulation passe par une revue au regard du cadrage §4.1 avant d'entrer au lexique.

**P4 — La catégorie de donnée est visible.** `mesuree` s'affiche sans qualificatif ; `estimee` porte toujours « estimation » ou « estimé » ; `prevue` porte toujours « prévision {source} » ; `declaree` cite l'acte (« arrêté préfectoral du … »). C'est la traduction visuelle de Spec 01 P4.

**P5 — L'horodatage affiché est celui de la donnée.** Le HTML porte les timestamps absolus **en UTC** (Spec 01 P7) ; la conversion en heure locale et les durées relatives (« il y a 3 h ») sont une surcouche JavaScript côté client — sans JS, l'UTC reste lisible et exact. L'heure de génération de la page n'apparaît nulle part.

**P6 — L'absence de donnée est une information.** Source en panne, trou de collecte, zone nuageuse : la page le dit (« météo momentanément indisponible — dernière mesure : … ») plutôt que d'afficher silencieusement une donnée périmée.

---

## 2. Lexique contractuel

Le cœur de la spec. Gabarits avec variables `{…}` ; chaque gabarit référence sa donnée source.

### 2.1 État et cycle de vie du feu

| Situation (donnée) | Libellé autorisé | Interdit |
|---|---|---|
| `lifecycle=actif`, détection au dernier passage | « Détecté au dernier passage satellite ({heure} UTC) » | « En cours », « hors de contrôle » |
| `lifecycle=actif`, pas de détection au dernier passage | « Aucune détection au dernier passage ({heure} UTC) — le suivi continue » | « En voie d'extinction » |
| `lifecycle=plus_detecte` | « Plus détecté depuis {N} heures (dernier hotspot : {date} {heure} UTC) » | « Éteint », « terminé », « maîtrisé » |
| Zone de cellule `front_actif` | « Zone détectée au dernier passage » | « Front de flammes » |
| Zone `recent` | « Zone détectée il y a {6–24} h » | — |
| Zone `plus_detecte` | « Zone plus détectée depuis plus de {24} h » | « Zone éteinte », « zone sécurisée » |
| `reprise=true` | « Nouvelles détections dans une zone précédemment silencieuse depuis le {date} » | « Reprise du feu » (terme SDIS, on ne l'affirme pas) |

Note transversale : « maîtrisé », « fixé », « éteint » sont des qualifications **opérationnelles des secours**. Vigifeu ne les emploie que s'il relaie une source officielle, alors citée : « La préfecture indique le {date} que le feu est fixé (communiqué n°{X}) » — catégorie `declaree`.

Note d'honnêteté sur « aucune détection au dernier passage » : c'est une **inférence**, pas une observation directe — nous constatons des détections ailleurs dans la même fenêtre de passage, pas la couverture réelle de la fauchée ni la nébulosité au-dessus de la zone. En bord de fauchée ou sous nuages, « pas de détection » peut signifier « pas d'observation ». Cette nuance est documentée dans la page méthodologie (cadrage §15bis), dans le même esprit que « plus détecté ≠ éteint ».

### 2.2 Mesures de dynamique

| Donnée | Gabarit |
|---|---|
| `front_progress_km/bearing` | « Le front de détection a progressé d'environ {N} km vers {direction cardinale} entre le {passage A} et le {passage B} » |
| FRP nuit/nuit ou jour/jour | « Intensité radiative totale : {X} MW au passage de {type} du {date}, contre {Y} MW au passage comparable précédent ({÷N} / {×N}) » |
| `area_ha_estimee` | « Emprise estimée d'après les détections : environ {N} ha (estimation satellite, non officielle) » |
| Surface officielle disponible | « Surface parcourue annoncée par {autorité} : {N} ha ({date}) » |

Jamais de comparaison FRP entre un passage de jour et un passage de nuit ; le générateur refuse le gabarit si les types diffèrent (garde-fou Spec 02 §6).

### 2.3 Vent et direction

| Donnée | Gabarit |
|---|---|
| `weather_obs` courant | « Vent {direction d'origine, ex. OSO} {V} km/h, rafales {R} km/h — mesure {provider} de {heure} UTC » |
| Vent + géométrie (fait composé) | « Le vent de {heure} UTC souffle en direction {cardinale} ; dans cette direction se trouvent {communes} » |
| Cône affiché sur carte | légende obligatoire : « Direction actuelle du vent (donnée météorologique) — ne représente ni une prévision ni une zone de propagation » |

Interdits absolus (cadrage §4.1) : « zone menacée », « propagation estimée », « la commune sera touchée », toute durée projetée (« dans N heures »).

### 2.4 Prévisions météorologiques (catégorie `prevue`)

Gabarit unique : « Prévision {provider}/{model} (run de {heure} UTC) : {contenu} » — ex. « Prévision Open-Meteo/AROME (run de 06:00 UTC) : 12 mm de pluie attendus sur la zone d'ici 48 h (probabilité 70 %) ».

Interdit : toute conclusion opérationnelle dérivée (« ce qui devrait aider les secours », « le feu devrait faiblir »).

### 2.5 Sécheresse et danger — barèmes de traduction

Les barèmes (versionnés dans le code, Spec 01 §3.5) traduisent la valeur brute en formulation, le chiffre restant en second niveau :

| Indicateur | Classes de traduction (initiales) |
|---|---|
| DC (sécheresse profonde) | < 100 « faible » / 100–300 « modérée » / 300–500 « élevée » / > 500 « très élevée » — formulé « Sécheresse profonde du terrain : {classe} » |
| FWI | reprendre les 6 classes EFFIS (très faible → très extrême) — « Danger météorologique d'incendie ({date}) : {classe} (indice FWI, Copernicus/EFFIS) » |
| Météo des forêts | classe officielle telle quelle : « Météo des forêts (Météo-France, {date}) : niveau {vert/jaune/orange/rouge} pour le département {dept} » |
| SIM (humidité des sols) | percentile vs normale saisonnière : « Humidité des sols : {très inférieure / inférieure / proche / supérieure} à la normale de saison (SIM, décade du {date}) » |
| VigiEau | citation de l'acte : « Commune en {niveau} sécheresse par arrêté préfectoral depuis le {date} » |

Les seuils DC ci-dessus sont des valeurs de départ à confronter à la littérature et aux pratiques Météo-France avant publication (point ouvert §7).

### 2.6 Latence et fraîcheur

Chaque page porte un bloc standard : « Les détections satellitaires parviennent avec un délai de traitement de 1 à 3 h après le passage ; un départ de feu peut précéder de plusieurs heures sa première détection ({lien méthodologie}). Dernière observation intégrée : {date heure} UTC. » Le délai moyen affiché deviendra la valeur **mesurée** de la saison (cadrage §6bis).

### 2.7 Attributions obligatoires (cadrage §5.8)

Bloc d'attributions en pied de chaque fiche (composant partagé, Spec 04 §2) : citation NASA FIRMS/LANCE/ESDIS avec lien vers le disclaimer (obligatoire dès qu'un hotspot est affiché ou exporté) ; « Limites administratives : IGN Admin Express, millésime {referentiel_millesime} » ; « Météo : Open-Meteo (CC BY 4.0) » (ou Météo-France selon la décision de lancement) ; « Historique incendies : BDIFF (min. Agriculture / IGN){, Prométhée le cas échéant} » ; attribution EUMETSAT en phase 2. Aucune formulation ne doit suggérer un endossement par un producteur de données.

---

## 3. Fiche feu — `/feux/{annee}-{slug}`

Structure de haut en bas ; chaque bloc cite ses tables sources (Spec 01).

**3.1 En-tête.** Nom (« Feu de {lieu principal}, {département} »), badge de cycle de vie (lexique 2.1), badge de niveau de confiance (§5.7 du cadrage), « Première détection : {first_acq_at} UTC », « Dernière observation : {last_acq_at} UTC ». Si fusion : mention « issu de la jonction de deux départs distincts ({liens}) ».

**3.2 Synthèse factuelle.** 3 à 6 phrases générées (lexique 2.1–2.4) : état, progression, intensité, vent, communes dans la direction du vent, prévision notable. C'est le paragraphe citable — presse et moteurs génératifs (Spec 04).

**3.3 Carte du feu.** Cellules colorées par ancienneté (validée en prototype), enveloppe, segment/cône de vent avec sa légende contractuelle, limites communales, POI majeurs (phase 2). Légende complète visible sans interaction.

**3.4 Communes concernées.** Liste issue de `fe_commune_rel`, groupée par type de relation : « emprise sur la commune » / « à moins de {5/10/20} km » / « dans la direction actuelle du vent ». Chaque commune cliquable vers sa fiche, avec l'intervalle de validité si la relation est fermée (« concernée du {date} au {date} »).

**3.5 Chronologie.** Un jalon par passage avec détections : date/heure, satellite, n hotspots (dédupliqués), FRP si comparable, événements de vie (création, fusion, reprise, requalification). Ordre antichronologique.

**3.6 Courbe d'intensité.** FRP total par passage, séries nuit et jour distinctes et visuellement séparées, avec la note « les passages de nuit et de jour ne sont pas comparables entre eux (sensibilité du capteur) ».

**3.7 Météo.** Observation courante (2.3) + prévision (2.4), chacune horodatée. Historique du vent aligné sur la chronologie 3.5 (le vent de chaque passage, pas le vent courant plaqué sur le passé).

**3.8 Contexte sécheresse.** FWI/DC du secteur, Météo des forêts du département (2.5).

**3.9 Données brutes (second niveau, replié).** Table des hotspots (coordonnées, acq, FRP, satellite, confiance source), GeoJSON téléchargeable, liens FIRMS/EFFIS. C'est aussi l'amorce de l'API.

**3.10 Encadré limites.** Rappel court : outil de veille, pas d'alerte ; sources officielles en cas de risque ; lien méthodologie et CGU.

**3.11 Mode archive.** À `lifecycle=archive` : bandeau « Feu archivé — dernière détection le {date} », relecture de propagation (versions successives animées ou par étapes), bilan chiffré, sources officielles de clôture si connues. La page ne bouge plus.

---

## 4. Fiche commune — `/communes/{code-insee}-{slug}`

**4.1 En-tête.** Nom, département, EPCI, population ({millésime}), lien communes limitrophes.

**4.2 Situation en cours.** Le bloc qui répond à « suis-je concerné ? » :

* aucun feu : « Aucun incendie suivi actuellement sur la commune ou à moins de 20 km (dernière observation satellite : {date heure} UTC) » ;
* sinon, par relation active : « L'incendie de {nom} ({lien}) a une emprise sur la commune » / « … est suivi à {N} km de la limite communale » / « La commune se trouve dans la direction actuelle du vent par rapport à l'incendie de {nom} ({horodatages}) ».

Ce bloc est le seul dont le contenu change en cycle courant (la page statique est toujours régénérée entière — Spec 02 §8) ; il est également régénéré quand une relation `direction_vent` s'ouvre ou se ferme au gré du vent.

**4.3 Contexte du jour.** Météo des forêts du département, niveau VigiEau de la commune, FWI/DC du secteur (lexique 2.5). Fraîcheur garantie par la régénération quotidienne post-`fetch_drought` (Spec 02 §8) — y compris pour les fiches « rien à signaler ».

**4.4 Historique incendies.** BDIFF (depuis 2006, France entière) et Prométhée (depuis 1973, arc méditerranéen — profondeur affichée selon le périmètre de la commune, cf. Spec 01 §5.3) : nombre de feux et surfaces par période, liste des événements notables (année, surface, type), plus les feux suivis par Vigifeu (liens fiches feux, y compris relations fermées : « concernée par le feu de Saumos du 22/07 au {date} »).

**4.5 Exposition structurelle.** Surface forestière et part du territoire, score d'exposition précalculé (méthode : module ultérieur — affiché seulement quand la méthode sera publiée dans la page méthodologie), comparaison aux communes voisines comparables.

**4.6 Réglementaire.** PPRIF (statut, référence), obligations légales de débroussaillement (statut, lien texte). Catégorie `declaree`, sources citées.

**4.7 Données brutes (second niveau).** Indices du jour en valeurs, exports, sources.

**4.8 Mode « rien à signaler ».** La fiche hors événement reste complète (4.3–4.6) : c'est la valeur hors saison et le maillage SEO (cadrage §8.5). Formulation neutre, jamais anxiogène ni rassurante au-delà du factuel (« aucune détection » ≠ « aucun risque »).

---

## 5. États particuliers (les deux fiches)

* **Requalification** (`faux_positif` après publication) : la page reste en ligne avec bandeau « Requalifié le {date} : les détections correspondent vraisemblablement à {motif} » — jamais de suppression silencieuse.
* **Fusion** : chronologie à deux origines, les anciennes URLs redirigent vers le feu absorbant avec ancre.
* **Source en panne** : bloc concerné en mode dégradé avec dernière valeur datée (P6), jamais de valeur sans date.
* **Signal MTG seul (phase 2)** : affiché **sur la carte nationale uniquement** (pas de fiche ni de `public_id` avant confirmation VIIRS — Spec 02 §5.2), niveau `probable`, libellé « Signal géostationnaire en attente de confirmation par satellite défilant » — jamais mêlé visuellement aux détections confirmées.

---

## 6. Ce que les fiches ne contiennent pas

Ni carte de risque prospectif, ni « zones menacées », ni délai projeté, ni conseil opérationnel (« évacuez », « restez chez vous »), ni relais de signalements non officiels. Les seules recommandations autorisées sont le renvoi aux autorités (encadré 3.10) — c'est l'application stricte du cadrage §4 et la matière de l'article 3 des CGU.

---

## 7. Points à trancher (entrées de la Spec 04)

1. Validation des seuils de traduction DC (2.5) contre littérature/pratiques Météo-France avant première publication.
2. Représentation du cône de vent : segment simple (v1, prototype) vs secteur angulaire ±{A_vent}° — cohérence avec la relation `direction_vent` de Spec 02 §7.
3. Relecture de propagation en mode archive : animation temporelle vs étapes cliquables (coût de génération statique).
4. Sources officielles dans les fiches (communiqués préfecture, points SDIS) : relayées en v1 (saisie manuelle ?) ou phase 2 (flux semi-officiels §5.7) — impact sur le lexique 2.1.
5. Périmètre exact du « rien à signaler » initial : communes BDIFF + concernées (retenu par défaut) ; critère d'entrée d'une nouvelle commune dans le périmètre.

---

*Prochain module : Spécification 04 — Générateur statique & SEO/GEO (gabarits, régénération sélective, JSON-LD, sitemap, maillage, performance, mesure de visibilité).*
