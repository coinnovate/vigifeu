# Projet **Vigifeu** (nom de travail)

## État d'avancement – Vision, conclusions et cadrage

**Version :** 0.4
**Périmètre au lancement :** France (extension Espagne envisagée)
**Vision cible :** Europe

**Principales évolutions depuis la v0.3 :** vérification des conditions de réutilisation des sources pour un service commercial (nouvelle section 5.8) — FIRMS et données Etalab libres avec attribution, contrainte Open-Meteo identifiée (API gratuite réservée au non-commercial), démarche de licence EUMETSAT « Service Provider » à effectuer pour le produit FIR ; correction de la couverture temporelle BDIFF (2006, France entière) vs Prométhée (1973, arc méditerranéen).

**Évolutions de la v0.3 :** validation technique de la chaîne complète par prototype sur données réelles (feu de Saumos/Le Porge, juillet 2026) ; latence satellitaire mesurée empiriquement et confirmée ; taxonomie de qualification à trois signatures issue de l'observation ; passage requis à un clustering spatio-temporel et à un rattachement communal multi-communes ; produit géostationnaire MTG-FCI identifié comme opérationnel et accessible ; sources sécheresse et pluie précisées ; architecture de stockage arrêtée (SQLite + Parquet, PostGIS en cible SaaS) ; principe de génération statique événementielle ; arborescence du site posée ; ingestion FIRMS robuste (quotas, timeouts).

---

# 1. Vision

Vigifeu est une plateforme SaaS de veille des incendies de végétation, conçue pour l'Europe et lancée d'abord sur la France.

L'objectif n'est pas de remplacer les services de secours ou les organismes publics, mais de transformer une multitude de données publiques en une information exploitable immédiatement par les professionnels.

Le produit s'adresse avant tout aux acteurs qui doivent comprendre rapidement :

* où se trouvent les incendies,
* comment ils évoluent,
* quels sites et quelles communes sont potentiellement concernés,
* quelles actions opérationnelles doivent être envisagées.

Le cœur du produit est une plateforme de veille géographique et non un outil de prévision.

**Principe directeur :** Vigifeu n'affiche pas plus de données que les outils existants, il les **traduit**. La donnée brute (FWI, FRP, coordonnées) reste accessible en second niveau pour les experts ; le premier niveau répond à la seule question que se pose l'utilisateur : *« Suis-je concerné, et qu'est-ce que ça implique ? »*

---

# 2. Positionnement

Le produit est volontairement spécialisé (incendies de végétation uniquement) et volontairement **B2B professionnel**.

Il ne s'agit pas d'une plateforme "multirisques". Chaque risque possède ses propres données, experts, organismes et utilisateurs. La spécialisation constitue un avantage commercial.

Le positionnement B2B est également un choix assumé face au paysage existant (cf. section 11) : le terrain de l'outil citoyen gratuit est déjà occupé en France par des acteurs associatifs ou anonymes. Vigifeu se différencie par :

* un éditeur identifié, des CGU, un engagement de service, une facture ;
* une rigueur de données de niveau contractuel ;
* une couche métier (sites surveillés, API, analyse communale) absente des outils grand public.

---

# 3. Zone couverte

## Lancement : France

La France offre au lancement :

* des référentiels géographiques d'excellente qualité (IGN Admin Express, geo.api.gouv.fr, codes INSEE, BD TOPO, BD Forêt) ;
* un historique incendies structuré (BDIFF depuis 2006 France entière ; base Prométhée depuis 1973 pour l'arc méditerranéen) ;
* un cadre réglementaire exploitable comme donnée (PPRIF, obligations légales de débroussaillement) ;
* un marché exposé et médiatisé.

## Extension prioritaire : Espagne

* ~8 100 municipios, codes INE, géométries via l'IGN espagnol ;
* marché très exposé (l'Espagne et le Portugal concentrent une part majeure des surfaces brûlées européennes) ;
* attention : compétences largement régionalisées (comunidades autónomas), l'interlocuteur public n'est pas toujours le municipio — mais pour les cibles privées, la logique communale reste identique.

## Vision cible : Europe

L'extension se fera **pays par pays**, comme une réplication méthodique du même modèle (référentiel communal + enrichissement local + réglementaire), et non comme une couverture cartographique uniforme.

**La différenciation n'est pas « on couvre plus large » mais « on connaît chaque commune mieux que quiconque ».** Un périmètre restreint permet une qualité de données d'enrichissement qu'un acteur pan-européen ne peut pas se permettre.

Langues : français au lancement, puis espagnol, puis anglais/allemand/italien/portugais au rythme de l'extension géographique.

---

# 4. Ce que Vigifeu n'est PAS

Vigifeu n'est pas :

* un outil de prévision des incendies ;
* un système officiel d'alerte civile ;
* un logiciel de commandement des pompiers ;
* un outil de décision de sécurité ;
* un système d'alerte précoce (voir section 6 sur la latence).

Il fournit une représentation enrichie de données publiques.

## 4.1 Frontière contextualisation / prédiction (à graver dans les specs)

La fonctionnalité la plus tentante du domaine est aussi la plus dangereuse : la carte des « zones menacées ». La frontière suivante est contractuelle et s'impose à toute l'interface :

| Autorisé (fait constaté ou source officielle) | Interdit (pronostic de sécurité) |
|---|---|
| « Vent actuel : OSO 14 km/h, rafales 38 » | « Zones menacées » |
| « Cône de direction du vent » présenté comme donnée météo | « Propagation estimée » |
| « La commune X se trouve dans la direction actuelle du vent » | « La commune X sera touchée dans N heures » |
| « Le front actif a progressé de N km en 6 h » (mesuré) | « Simulation de propagation » |
| « Zone plus détectée depuis N heures » (observation) | « Zone éteinte » |
| « Prévision Météo-France/ECMWF : 12 mm attendus sous 48 h » | « Le feu sera éteint demain » |

Tout élément visuel projectif (cône, flèche) doit être libellé comme une donnée météorologique factuelle, jamais comme une évaluation de menace. Cette règle est un différenciant de sérieux, pas une limitation.

**Précisions issues du prototype (nouveau) :**

* **« Plus détecté » ≠ « éteint ».** L'absence de détection à un passage peut résulter d'un nuage, du panache de fumée, de l'angle de visée ou d'un feu sous le seuil de sensibilité — et une zone silencieuse peut se réactiver (sautes, reprises). La formulation contractuelle est « plus détecté depuis N heures », jamais « éteint ». La prudence de vocabulaire est un différenciant face aux outils qui affichent « feu éteint » sur la foi d'une absence.
* **Les prévisions météorologiques officielles sont autorisées** (pluie, vent prévu) car ce sont des prévisions d'organismes officiels, pas des pronostics de menace produits par Vigifeu. Elles doivent être libellées comme telles, avec leur source, et jamais traduites en conclusion opérationnelle.

---

# 5. Sources de données

## 5.1 Détection — NASA FIRMS

* hotspots VIIRS (375 m) et MODIS (1 km), flux NRT.
* Trois satellites porteurs de VIIRS : Suomi-NPP (2011), NOAA-20 (2017), NOAA-21 (2022) — même instrument, même algorithme, passages décalés d'environ 50 minutes sur le même plan orbital. Chacun survole la France deux fois par jour (fenêtre nocturne ~01h30–03h30 UTC, fenêtre de mi-journée ~12h–14h UTC).

**Contrainte structurante :** le flux ultra temps réel (URT, < 60 s) de FIRMS n'existe que pour les États-Unis et le Canada. Pour l'Europe :

* passages VIIRS : 2 à 4 par jour selon la latitude ;
* traitement NRT (LANCE) : 1 à 3 heures après le passage ;
* **délai total possible dans le pire cas : 6 à 8 heures** — désormais **confirmé empiriquement** (cf. section 6bis).

**Enseignements opérationnels du prototype (nouveau) :**

* **Redondance multi-satellites** : SNPP et NOAA-20 observent la même scène à ~15–20 min d'écart et produisent des hotspots quasi dupliqués. Il faut dédupliquer (ou raisonner par passage), sous peine de gonfler artificiellement comptages et FRP totaux. Ajouter NOAA-21 densifie encore la couverture.
* **Liste de sources configurable, jamais codée en dur** : Suomi-NPP a dépassé sa durée de vie nominale et son orbite dérive ; des satellites disparaîtront, d'autres arriveront.
* **Quotas et résilience de l'API** : la MAP_KEY FIRMS est limitée (~5 000 transactions / 10 min, les grosses requêtes comptant multiple) et le serveur ralentit précisément les jours de grands feux (charge + volume). Conséquences d'architecture : requêtes découpées jour par jour, timeouts généreux, retries, et surtout **ingestion continue en tâche de fond** (collecte permanente stockée chez nous) plutôt que des requêtes à la demande au moment de la consultation.

## 5.2 Détection — Géostationnaire

Meteosat (MTG-FCI, EUMETSAT) observe l'Europe en continu avec un rafraîchissement de l'ordre de 10 minutes, à résolution kilométrique (canal feu à 3,8 µm ; ~2 km effectifs à la latitude française compte tenu de l'angle de visée).

* Ne détecte que les feux déjà significatifs, mais comble le trou temporel du VIIRS.
* Architecture cible : **fusion géostationnaire + VIIRS** — le géostationnaire donne le *quand* et la tendance d'intensité, le VIIRS donne le *où précisément* et le périmètre au passage suivant. Différenciant réel face aux outils qui n'affichent que FIRMS.

**Mise à jour (nouveau) :** un produit de niveau 2 « Active Fire Monitoring » (FIR) dérivé de FCI existe et est **opérationnel**, généré toutes les 10 minutes, distribué via le Data Store d'EUMETSAT. **Point de licence (vérifié, cf. 5.8)** : la politique de données EUMETSAT distingue les produits « essentiels » (libres) des produits « recommandés » (sous licence), et définit une catégorie « Service Providers » — fournisseurs de services à valeur ajoutée à des tiers, ce qui est exactement le cas de Vigifeu. La plupart des licences sont gratuites, mais des frais peuvent s'appliquer pour l'usage commercial : la classification exacte du produit FIR et l'obtention de la licence adéquate sont une **démarche formelle à effectuer avant la phase 2** (en même temps que le test technique ci-dessous). Impact attendu inchangé : pour les feux significatifs, latence ramenée de plusieurs heures à quelques dizaines de minutes, et suivi réellement continu entre les passages VIIRS. Limites à afficher honnêtement : seuil de détection élevé (pas les petits départs), résolution kilométrique (pas d'attribution communale fine sur un seul pixel), aveuglement par nuages épais, faux positifs propres (reflets solaires) — d'où intégration au niveau « probable » de la hiérarchie de confiance (5.7), promu « confirmé » à la confirmation VIIRS.

Piste ultérieure (hors MVP) : antenne de réception directe VIIRS en Europe, sur le modèle du SSEC américain.

**Prochain test technique identifié :** récupérer le produit FIR sur le Data Store pour la période du 22 au 25 juillet 2026 (Gironde) et le comparer au déroulé VIIRS du feu de Saumos — validation de la brique sur le feu de référence.

## 5.3 Copernicus

* EFFIS (dont FWI quotidien — **ne pas recalculer, consommer**) ;
* occupation des sols (Corine Land Cover, couche forêt) — désormais également identifiée comme intrant du filtre de qualification (masques industriels, cf. section 7) ;
* données Sentinel.

**Précision (nouveau) :** les « feux actifs » affichés par EFFIS sont les mêmes hotspots MODIS/VIIRS de la NASA, redistribués. Consommer FIRMS directement est généralement plus frais et plus simple pour le quasi temps réel ; EFFIS reste la source pour le FWI et ses sous-indices.

## 5.4 Météo

* Open-Meteo (gratuit, basé ECMWF/ICON/AROME, API simple) : suffisant pour le MVP ;
* **contrainte de licence (vérifiée, cf. 5.8)** : l'API gratuite d'Open-Meteo est réservée aux usages **non commerciaux** — le prototypage et la R&D sont couverts, mais le lancement commercial exigera soit l'abonnement payant (~29–99 $/mois, licence commerciale et serveur dédié inclus), soit la bascule sur les sources primaires Météo-France en open data (Licence Ouverte 2.0, gratuite y compris en usage commercial). Décision à prendre au lancement ; les données elles-mêmes sont en CC BY 4.0 (attribution requise) ;
* vent, rafales, direction, humidité, **pluie (observée récente, prévision horaire 7–16 jours, probabilité)**, température.

**Le vent est la donnée reine** : croisé avec la géométrie du FireEvent, il produit le « cône de direction du vent », probablement la fonctionnalité qui vendra le produit (dans le strict respect de la section 4.1). Points d'implémentation issus du prototype : le vent affiché doit être horodaté et cohérent avec l'horodatage des détections (pas un vent « actuel » plaqué sur un historique) ; à terme, vent au front plutôt qu'au centroïde pour les grands feux.

**Montée en gamme identifiée :** modèles AROME/ARPEGE en open data sur meteo.data.gouv.fr (fichiers GRIB, plus lourds) et lame d'eau radar, si le besoin de source primaire se confirme.

## 5.4bis Sécheresse et état du terrain (nouveau)

Empilement de sources, du plus opérationnel au plus structurel :

* **FWI et ses sous-indices (EFFIS)** : FFMC (sécheresse des combustibles fins de surface, réactivité immédiate), DMC (litière), **DC / Drought Code (sécheresse profonde accumulée — l'indicateur « niveau de sécheresse du terrain »)**. Quotidien, Europe entière, avec prévision. Traduction métier obligatoire : « sécheresse profonde très élevée pour la saison », le chiffre brut en second niveau — ne pas reproduire l'erreur des outils qui affichent « DC : 456 ».
* **Météo des forêts (Météo-France)** : carte quotidienne officielle de danger feu par département, quatre niveaux — citable en référence dans les fiches.
* **Indice d'humidité des sols SIM (Météo-France, open data)** : référence des reconnaissances catastrophe naturelle ; ~8 km, décadaire — pour la fiche commune et l'analyse hors saison, pas le temps réel.
* **VigiEau / Propluvia** : arrêtés de restriction d'eau — sécheresse hydrologique officielle, communale, historisée. Élément de contexte d'exposition pour la fiche commune, gratuit en calcul.

## 5.5 Données géographiques et POI

* Référentiel communal : IGN Admin Express, geo.api.gouv.fr, codes INSEE (France) ; IGN espagnol, codes INE (Espagne) ;
* Historique incendies : BDIFF (France entière, depuis 2006, maille communale, produite par l'IGN pour le ministère de l'Agriculture) ; base Prométhée (arc méditerranéen, depuis 1973) pour la profondeur historique — le libellé « historique depuis 1973 » n'est valable que sur ce périmètre ;
* Réglementaire : PPRIF, obligations légales de débroussaillement ;
* POI : OpenStreetMap (campings, écoles, hôpitaux, stations-service — couverture correcte en Europe de l'Ouest) complété par les bases nationales (BD TOPO) ;
* routes, forêts, réserves naturelles, infrastructures, établissements sensibles, entreprises.

**Enjeu qualité :** le vrai travail n'est pas l'accès mais la fraîcheur et la qualification. Un camping OSM fermé depuis trois ans qui reçoit des notifications détruit la crédibilité auprès d'un client payant.

## 5.6 Sites déclarés par les clients (donnée cœur)

Les clients déclarent et géolocalisent leurs propres sites (établissements, actifs, portefeuilles). C'est **la donnée la plus précieuse du système** : leurs actifs à eux, que personne d'autre n'a. Elle est au cœur du modèle économique (cf. section 15).

## 5.7 Sources tierces et niveaux de confiance

L'architecture distingue dès le départ les niveaux de confiance :

1. **Confirmé** — détection satellite VIIRS/MODIS qualifiée ;
2. **Probable** — signal géostationnaire (MTG-FCI FIR) en attente de confirmation ;
3. **Signalement tiers** — sources externes non satellitaires.

Cette structure permet de brancher ultérieurement, sans refonte :

* des flux semi-officiels (comptes des SDIS, Copernicus EMS, arrêtés préfectoraux) — *un signalement du compte du SDIS 13 vaut mieux que dix signalements anonymes* ;
* éventuellement une couche communautaire (cf. section 10).

## 5.8 Conditions de réutilisation pour un service commercial (nouveau — vérifiées juillet 2026)

| Source | Régime | Obligations pour Vigifeu | Statut |
|---|---|---|---|
| **NASA FIRMS** (hotspots VIIRS/MODIS) | Données ouvertes, usage commercial explicitement autorisé | Citation LANCE/FIRMS/ESDIS ; reproduction ou lien vers le disclaimer NASA lors de toute fourniture à des tiers ; ne jamais suggérer un endossement par la NASA | ✅ Libre, attribution à intégrer (méthodologie + CGU) |
| **IGN** (Admin Express, BD TOPO, BD Forêt) | Licence Ouverte Etalab 2.0 depuis le 01/01/2021 — usage commercial expressément autorisé, y compris inclusion dans un produit propre | Mention du producteur et de la date de dernière mise à jour (champ `referentiel_millesime`, Spec 01) | ✅ Libre |
| **BDIFF** | Licence Ouverte 2.0 (data.gouv.fr) ; produite par l'IGN pour le ministère de l'Agriculture | Mention de la source. **Couverture réelle : depuis 2006, France entière, maille communale** — la profondeur 1973 relève de Prométhée (arc méditerranéen) | ✅ Libre ; intégration Prométhée à décider |
| **Open-Meteo** | Données CC BY 4.0 (commercial autorisé avec attribution) **mais API gratuite réservée au non-commercial** — un SaaS payant est explicitement un usage commercial | Phase actuelle (R&D) : couverte par l'offre gratuite. Lancement : abonnement (~29–99 $/mois) **ou** bascule Météo-France open data (LO 2.0) | ⚠️ Décision au lancement |
| **EUMETSAT** (MTG-FCI FIR) | Politique par produit (« essentiel » libre / « recommandé » sous licence) ; catégorie « Service Provider » pour les services à valeur ajoutée ; licences majoritairement gratuites, frais possibles en commercial | Vérifier la classification du produit FIR au Data Store et obtenir la licence Service Provider | 🔲 Démarche formelle avant phase 2 |
| **EFFIS/Copernicus** (FWI), **Météo-France / VigiEau** | Non encore vérifiés en détail (Copernicus : libre avec attribution a priori ; open data étatique : LO 2.0 a priori) | — | 🔲 À vérifier avant lancement |

Ce registre alimente directement la page « Méthodologie & sources » (15bis) et l'annexe des CGU. Règle générale retenue : chaque source affichée porte son attribution exacte, et aucune formulation du site ne suggère un endossement par un producteur de données.

---

# 6. Latence : conséquences sur le positionnement

La latence satellitaire (section 5.1) n'est pas un détail technique, elle définit les segments adressables :

* **Segments « temps critique »** (campings en zone rouge, communes en alerte) : le terrain, la fumée et les réseaux sociaux iront souvent plus vite que le satellite. Pour eux, Vigifeu est un outil de **suivi et de contexte**, pas d'alerte précoce. L'argumentaire commercial ne doit jamais promettre « dès la détection » sans qualifier le délai.
* **Segments « quelques heures acceptables »** (assurances, gestionnaires d'actifs, exploitants forestiers, médias, bureaux d'études) : la proposition tient parfaitement telle quelle.

Le segment de lancement est choisi **en fonction de cette contrainte, pas contre elle** (cf. section 12).

## 6bis. Validation empirique — cas du feu de Saumos / Le Porge, juillet 2026 (nouveau)

Le prototype (section 7bis) a permis de mesurer la latence sur le plus gros feu français de la saison :

* Départ de feu officiel : mercredi 22 juillet 2026 dans la matinée, commune de Saumos (Gironde) ; plus de 800 ha parcourus le premier jour ; ~20 000 personnes concernées par les évacuations préventives sur quatre communes (Saumos, Le Porge, Le Temple, Lège-Cap-Ferret).
* Les passages VIIRS nocturnes du 22 (01h–03h UTC), antérieurs à l'allumage, ne montrent rien : le feu est parti juste après une fenêtre de passage — malchance orbitale quasi maximale.
* Première acquisition VIIRS du foyer : 22 juillet, 12:32 UTC (14h32 locale), soit **3 à 6 heures après l'allumage**, auxquelles s'ajoute le traitement NRT (1 à 3 h) avant disponibilité dans FIRMS.
* **Latence totale vécue par l'utilisateur : de l'ordre de 5 à 9 heures.** L'estimation « 6 à 8 h dans le pire cas » de la v0.2 est confirmée.
* Trou d'observation mesuré en phase de suivi : **près de 12 heures** sans aucune donnée entre le passage de mi-journée du 24 et le passage nocturne du 25 — sur un feu en cours d'évacuation massive. C'est l'argument chiffré du géostationnaire (5.2).
* Pendant les premières heures (départ → première disponibilité), le camping de La Grigne (Le Porge) était déjà évacué (3 500 personnes) : la segmentation de la section 6 est validée par les faits — inutilisable en alerte pour le « temps critique », pleinement utile en suivi pour les segments cibles.

**Reste à mesurer :** la composante « traitement NRT » précise et la distribution complète de latence sur une saison. Protocole défini : interrogation FIRMS toutes les 15 minutes avec enregistrement de l'heure de première apparition de chaque hotspot, comparée à son heure d'acquisition. Automatisable dès maintenant ; alimentera la page méthodologie (section 15bis) avec des délais honnêtes et chiffrés.

---

# 7. Qualification des détections

Les publications scientifiques montrent que FEDS réalise déjà : regroupement des hotspots, suivi d'un incendie, calcul de périmètre, évolution temporelle, front actif. Le flux opérationnel complet reste principalement disponible pour l'Amérique du Nord ; pour l'Europe, nous développons notre propre moteur de regroupement à partir des hotspots VIIRS. Ce point est confirmé comme non bloquant (le prototype le réalise, cf. 7bis).

Le vrai défi n'est pas le clustering mais la **qualification** : FIRMS est plein de faux positifs (torchères industrielles, brûlages agricoles, panneaux solaires, aciéries). Le filtre « est-ce un vrai feu de végétation qui mérite une notification ? » fait la différence entre un produit crédible et un produit qui spamme ses clients.

## 7.1 Taxonomie de qualification à trois signatures (nouveau — issue de l'observation)

L'analyse de 7 jours de données réelles France entière (6 749 hotspots, 341 événements) fait émerger trois signatures spatio-temporelles discriminantes :

| Signature | Comportement observé | Interprétation | Exemples observés |
|---|---|---|---|
| **Persistant-fixe** | Présent sur plusieurs jours, emprise stable (< quelques centaines de mètres), FRP unitaire faible | Source industrielle (torchère, aciérie, raffinerie) | Grande-Synthe (aciéries de Dunkerque), Fos-sur-Mer, Martigues, Port-Jérôme, Trith-Saint-Léger, triangle Hayange/Florange/Serémange |
| **Éphémère-unique** | Apparition unique, 1–2 hotspots, faible FRP, jamais revu au même endroit | Brûlage agricole, petit feu maîtrisé, bruit | Myriade de détections isolées en zones de grandes cultures fin juillet |
| **Persistant-mobile** | Persiste **et** s'étend/se déplace entre les passages | Vrai feu de végétation | Saumos/Le Porge, Biscarrosse, Pontevès, Corte |

Enseignements structurants :

* **La profondeur temporelle est elle-même un outil de qualification** : une heuristique « présent sur N jours distincts + emprise stable + FRP unitaire faible » capte la quasi-totalité des sources industrielles sans même recourir à l'occupation des sols.
* **Précalculer une carte des sources fixes** : avec des mois d'historique, les sites industriels de France se cartographient une fois pour toutes et s'excluent d'office (avec réévaluation périodique).
* Le croisement avec Corine Land Cover et les masques industriels reste le second étage du filtre ; les calendriers de brûlage agricole, le troisième.
* Attention aux heuristiques trop étroites : un complexe industriel a plusieurs torchères espacées ; un critère d'emprise trop serré (< 500 m) laisse passer les grands sites — constaté sur données réelles.

## 7bis. Validation technique par prototype (nouveau)

Un prototype jetable (juillet 2026, ~350 lignes Python, dépendances requests + folium) a validé la chaîne complète sur données réelles :

**hotspots FIRMS (VIIRS SNPP + NOAA-20, 7 jours, France entière) → clustering → qualification heuristique → rattachement commune (geo.api.gouv.fr) → vent (Open-Meteo) → carte HTML interactive + export JSON.**

Résultats : détection et suivi corrects des feux réels de la période (Saumos/Le Porge, Biscarrosse, Pontevès, Corte, Mérignac), premiers filtres de faux positifs fonctionnels, rattachement communal opérationnel, mesure de latence (6bis), mesures de progression et d'intensité (7ter). **La faisabilité technique est validée de bout en bout.**

Limites identifiées, devenant exigences de spécification :

1. **Clustering spatio-temporel obligatoire.** Le clustering purement spatial sur données cumulées fusionne des événements distincts par chaînage (constaté : un petit événement à 12,6 km et 2 jours d'écart absorbé dans le feu de Saumos, faussant sa date de première détection). La date de naissance d'un FireEvent est une donnée contractuelle : elle doit être juste.
2. **Rattachement communal par intersection de géométries, multi-communes.** Le rattachement au centroïde attribue une commune unique à un feu qui en concerne quatre (et l'information officielle — évacuations, communiqués — est communiquée commune par commune). La relation FireEvent ↔ Commune est un ensemble {feu dans la commune / à moins de X km / dans la direction du vent}, pas un point.
3. **Déduplication inter-satellites** (cf. 5.1).
4. **Distinction front actif / surface parcourue** (cf. 7ter).

## 7ter. Cycle de vie et mesures factuelles d'un feu (nouveau)

Le prototype démontre que les données publiques suffisent à produire les « faits constatés » de la section 4.1, automatiquement :

* **Front actif vs surface parcourue** : découpage du feu en cellules (~750 m) classées par ancienneté de dernière détection — « détecté au dernier passage » / « détecté il y a 6–24 h » / « plus détecté depuis > 24 h ». Les seuils sont calés sur le rythme des passages VIIRS et devront être revus avec l'arrivée du flux MTG (10 min).
* **Progression mesurée** : « le front a progressé de 5,5 km vers le nord en 25 h » (mesuré sur Saumos entre deux passages nocturnes).
* **Courbe d'intensité** : FRP total par passage — en ne comparant que passages comparables (nuit avec nuit, jour avec jour : la sensibilité du capteur diffère). Mesuré sur Saumos : intensité divisée par plus de dix entre les nuits du 24 et du 25, cohérente avec la progression des secours.

Cette matière (état par zone, progression, intensité, communes touchées, horodatage complet) constitue le contenu de la fiche incendie — sans un gramme de prédiction — et n'est agrégée sous cette forme par aucun outil du paysage concurrentiel.

---

# 8. Architecture envisagée

Le cœur du système repose sur **deux objets de première classe**.

## 8.1 FireEvent

Chaque incendie est un objet vivant contenant :

* identifiant interne
* géométrie
* historique **versionné** (chaque état successif est conservé — la relecture de propagation est une fonctionnalité)
* hotspots (avec niveau de confiance par source, cf. 5.7, dédupliqués inter-satellites)
* vitesse et direction mesurées ; courbe d'intensité par passage (7ter)
* état, dont le cycle de vie factuel : front actif / zones plus détectées (jamais « éteint »)
* statistiques
* statut de qualification (confirmé végétation / suspect source fixe / suspect détection isolée / faux positif) selon la taxonomie 7.1

Autour se greffent : météo (horodatée de manière cohérente avec les détections), communes concernées (multi-communes, par intersection), infrastructures, notifications.

## 8.2 Commune

Constat : tous les outils existants montrent « des cartes, des points, mais pas de communes ». Or les autorités, les médias et les habitants raisonnent en communes ; les satellites raisonnent en pixels. **Faire le pont entre les deux est précisément notre couche de contextualisation.** (Confirmé sur le feu de Saumos : toute l'information opérationnelle — évacuations préventives, points de situation préfectoraux — a été communiquée commune par commune, pour quatre communes, quand une carte de hotspots n'en désigne aucune.)

Chaque commune est un objet à part entière :

* identifiant stable (code INSEE / INE), géométrie, population, EPCI ;
* précalculs : surface forestière, exposition structurelle, communes voisines comparables ;
* historique des feux (BDIFF) ;
* statut réglementaire (PPRIF, obligations de débroussaillement) ; contexte sécheresse (SIM, VigiEau — cf. 5.4bis) ;
* **relation FireEvent ↔ Commune calculée par intersection de géométries et historisée en permanence** : feu dans la commune, feu à moins de X km, commune dans la direction actuelle du vent, axes routiers concernés.

La fiche commune répond directement à la question « *et Mérignac, elle est concernée ou pas ?* » — question à laquelle une carte de hotspots ne répond jamais.

Valeur hors saison : historique décennal, exposition, comparaisons — contenu vendable toute l'année (cf. section 12, saisonnalité).

## 8.3 Rigueur des données comme barrière à l'entrée

L'observation des outils existants montre que les briques fonctionnelles sont assemblables par un petit acteur (le prototype interne le confirme : chaîne complète en quelques centaines de lignes). Notre barrière à l'entrée n'est pas fonctionnelle, elle est dans la rigueur :

* cohérence des unités partout (jamais « 300 km² » ici et « 30 000 ha » là) ;
* traçabilité de chaque chiffre (source, horodatage) ;
* horodatage honnête : les pages affichent **l'horodatage de la donnée** (« dernière observation satellite : 03:30 UTC »), jamais l'heure de génération de la page ; les durées relatives (« il y a X heures ») sont calculées côté client ;
* distinction nette entre **donnée mesurée**, **donnée déclarée**, **estimation** et **prévision (source météorologique officielle)** — quatrième catégorie ajoutée en v0.3 ;
* indicateurs de confiance qui discriminent réellement (un « 100 % » partout ne dit rien).

Pour un client B2B qui paie, chaque approximation est un motif de résiliation.

## 8.4 Architecture technique de stockage et d'ingestion (nouveau)

Décisions arrêtées pour la phase actuelle (pré-SaaS) :

* **Ingestion continue en tâche de fond** : collecte permanente des sources (FIRMS jour par jour, MTG à terme), stockage local — jamais de requête aux sources au moment de la consultation. Motivation constatée : les sources ralentissent précisément les jours de grands feux ; robustesse quotas/timeouts/retries intégrée dès l'ingestion.
* **État vivant : SQLite** (mode WAL — un processus écrivain d'ingestion, des lecteurs), contenant les FireEvents en cours (7–14 jours glissants), le référentiel communal et les relations précalculées. Volumes dérisoires à l'échelle France (~1–2 M hotspots/an) : SQLite est largement dans sa zone de confort. SpatiaLite en option pour le spatial.
* **Archive : fichiers Parquet partitionnés par jour/mois** (GeoParquet pour les géométries), interrogés par DuckDB. L'archive n'est pas un grenier, c'est un produit : l'historique doit rester *requêtable* (fiches communes décennales, analyse d'exposition, valeur hors saison) sans serveur ni rechargement.
* **Cible SaaS : PostgreSQL + PostGIS** pour l'état vivant, le jour où arrivent clients multiples, API, workers de notifications et sites déclarés. Le critère décisif n'est pas le « relationnel » mais le **spatial** (point-dans-polygone contre 35 000 communes, distances indexées, intersections) — PostGIS est le standard du métier. Migration triviale si le schéma est propre ; l'archive Parquet ne bouge pas.
* **Historiser dès le premier jour ce qui ne se reconstruit pas** : heure d'ingestion de chaque hotspot (mesure de latence), versions successives des FireEvents (relecture de propagation), relation feu-commune horodatée. Le choix du moteur est réversible à tout moment ; ces données-là, non.

## 8.5 Site : génération statique événementielle (nouveau)

Principe : les données arrivent par paquets (passages satellite), pas en continu. Les pages publiques sont donc **régénérées à l'événement** (juste après chaque ingestion), pas mises en cache à expiration — fraîcheur d'un site dynamique, robustesse et coût d'un site statique.

* **Régénération sélective** : après un passage, seules changent la carte nationale, les fiches des feux actifs et les fiches des communes concernées (quelques dizaines de pages) ; les fiches « rien à signaler » gardent leur version (régénération nocturne ou à la demande).
* **Horodatage honnête** : cf. 8.3 — timestamps absolus de la donnée dans la page, durées relatives calculées côté client.
* **Deux couches** : socle public pré-généré (cartes, fiches feux, fiches communes) + couche personnalisée servie dynamiquement (future partie abonnés — sites déclarés, notifications, portefeuilles : dynamique par nature et confidentielle, jamais dans un cache partagé).
* Le schéma tient à l'arrivée du flux MTG (10 min) : seules les fiches des feux actifs se régénèrent plus souvent, pas le socle.
* Bénéfice induit : les fiches communes statiques sont indexables — acquisition organique hors saison (« historique incendies <commune> »).

## 8.6 Arborescence du site (nouveau — version sans espace abonné)

Décision : **pas de partie abonnés dans la première version publique** ; l'arborescence reste aussi resserrée que le positionnement (pas de blog, pas de multirisques).

* **Carte nationale** (accueil) — feux en cours, cycle de vie coloré, niveaux de confiance.
* **Fiche feu** — `/feux/{annee}-{slug}` (ex. `/feux/2026-saumos`) : état, front actif, chronologie, intensité, vent, communes concernées (cliquables). URL permanente et citable (presse, rapports) ; à l'extinction, la page devient l'archive du feu avec relecture de propagation.
* **Fiche commune** — `/communes/{code-insee}-{slug}` (ex. `/communes/33333-le-porge`) : situation en cours, historique BDIFF, exposition, réglementaire, sécheresse, feux passés et en cours (cliquables). Le code INSEE garantit unicité et pérennité, le slug la lisibilité.
* **Boucle de navigation croisée** feu ↔ communes : c'est la relation 8.2 rendue visible.
* **Saison & historique** — bilan, relecture des feux passés.
* **Méthodologie & sources** — cf. 15bis : page de confiance, pas une annexe.
* **Mentions légales & CGU** — éditeur identifié (cf. 15).

Déploiement progressif des fiches communes : d'abord les communes à historique BDIFF ou concernées par un feu (quelques milliers), pas les ~35 000 d'un coup. La couche abonnés se greffera comme une branche supplémentaire sans toucher au socle.

---

# 9. Fonctionnalités envisagées

## Carte interactive
Visualisation des incendies, avec niveaux de confiance visibles et **cycle de vie par ancienneté de détection** (front actif / récent / plus détecté — validé en prototype).

## Fiche incendie
Premier niveau : interprétation métier (« conditions modérément favorables à la propagation », « le vent pousse le feu vers le nord-est, en direction de X et Y »). Second niveau : données brutes (FWI, FRP, coordonnées) pour les experts. Contenu factuel démontré disponible : front actif, progression mesurée, courbe d'intensité par passage, communes concernées (cf. 7ter).

## Fiche commune
Situation en cours, historique, exposition, réglementaire, contexte sécheresse, sites présents.

## Historique
Relecture de la propagation (versions successives du FireEvent) ; historique communal pluriannuel.

## Notifications
Notification lorsqu'un incendie apparaît, s'approche d'un site surveillé ou évolue fortement — avec délai de détection affiché honnêtement. **Notifications B2B contractualisées, aide à la décision — jamais un dispositif d'alerte grand public ni de sécurité des personnes (cf. Spec 05 §0).**

## Analyse géographique
Quelles communes ? quels campings ? quels établissements ? quels clients ? quels axes routiers ?

## Sites surveillés
Déclaration et géolocalisation par le client de ses propres actifs et portefeuilles. (Hors première version publique ; cf. 8.6.)

## API
Accès automatisé aux données.

## Priorisation

1. **Sites surveillés déclarés par l'abonné** — cœur du modèle économique ;
2. **Fusion vent + géométrie** (cône de direction du vent) — différenciant visible ;
3. **Géostationnaire (MTG-FCI FIR)** — réponse à la latence, produit opérationnel identifié ;
4. **Communautaire** — en réserve (cf. section 10).

---

# 10. Couche communautaire : prévue mais non incluse

Décision : **pas de signalement communautaire en version 1.** Trois raisons :

1. **Responsabilité** : notre positionnement juridique repose sur « nous relayons des données publiques officielles ». Afficher des signalements d'utilisateurs nous rendrait éditeurs d'information non vérifiée sur un sujet de sécurité civile (faux signalement → panique ; vrai feu « invalidé » par la foule → pire).
2. **Démarrage à froid** : une couche communautaire sans masse critique affiche du vide et décrédibilise le produit.
3. **Coût opérationnel** : modération 24/7 en pleine saison, multilingue.

Par ailleurs, la place du « Waze du feu » citoyen et gratuit est déjà occupée en France (feuxdeforet.fr, associatif — cf. section 11), et il serait difficile et peu pertinent de rivaliser avec une association bénévole sur ce terrain.

L'architecture multi-confiance (5.7) préserve néanmoins la possibilité de brancher plus tard soit du communautaire (avec cadre juridique révisé), soit — plus intéressant — des flux semi-officiels. Un partenariat avec les acteurs associatifs existants (leurs signalements terrain comme source tierce « basse confiance ») est envisageable à terme.

---

# 11. Paysage concurrentiel

## EFFIS (Copernicus) — gratuit
Suivi de feux actifs à l'échelle européenne, FWI quotidien. **La réponse à « pourquoi payer alors qu'EFFIS existe ? » doit être limpide dès le premier pitch** : EFFIS montre les feux (les mêmes hotspots NASA, redistribués), Vigifeu dit qui est concerné (sites du client, communes, notifications personnalisées, API, interprétation métier).

## OroraTech (Munich)
Wildfire monitoring avec nanosatellites propriétaires. Valide le marché ; positionné sur la détection, pas sur la couche métier communale européenne.

## ICEYE
Donnée catastrophe pour assureurs. Concurrent potentiel sur le segment assurance, complémentaire ailleurs.

## feuxdeforet.fr (France)
Plateforme participative citoyenne, associative (loi 1901), gratuite, sans objectif commercial. Signalements modérés par la communauté + contenu éditorial. Occupe le terrain citoyen ; partenaire potentiel plutôt que concurrent pour du B2B.

## alerteforet.fr (France)
Agrège détections, météo Open-Meteo, FWI, chronologies, avec entrée « commune ». Enseignements de l'analyse détaillée :

* **fonctionnellement proche de nos idées** (preuve que les briques sont assemblables) ;
* **mais trop technique pour le grand public et les collectivités** (FWI/FFMC/DMC bruts, FRP, GPS décimal — aucune traduction métier) ;
* **qualité de données insuffisante pour du B2B** (incohérences d'unités, confiance uniforme à 100 %, historique mélangeant des feux distincts, fraîcheur douteuse) ;
* **anonyme, sans mentions légales visibles** (infraction LCEN en soi) ;
* propose une « simulation de risque / zones menacées » anonymement pendant des feux réels — exactement le champ de mines que notre section 4.1 interdit.

## Synthèse
Le marché est validé, le terrain citoyen est pris, le terrain « détection spatiale » est pris par des acteurs capitalisés. **L'espace libre est le B2B métier, communal, juridiquement solide, rigoureux sur la donnée.** C'est notre positionnement. Le prototype confirme au passage qu'aucun outil du paysage n'agrège front actif, progression mesurée, intensité et communes concernées sous une forme exploitable (7ter).

---

# 12. Clients potentiels et priorisation commerciale

## Segments prioritaires

1. **Assurances** : budget, besoin quantifiable (exposition du portefeuille), tolérance à la latence satellitaire, valeur hors saison (analyse d'exposition) ;
2. **Gestionnaires de réseaux et d'infrastructures** (électricité, autoroutes, télécom, ferroviaire) : mêmes caractéristiques ;
3. **Exploitants forestiers, gestionnaires d'actifs, bureaux d'études** ;
4. **Médias** : vision consolidée, fiches communes et fiches feux citables (URLs permanentes, cf. 8.6).

## Segments en second temps

* **Collectivités** (communes, départements, régions) : cible naturelle de la fiche commune, mais cycles de vente longs, budgets faibles, appels d'offres — pas un segment de démarrage ;
* **Tourisme** (campings, villages vacances) : segment « temps critique » — argumentaire à reformuler autour du suivi et du contexte, pas de l'alerte précoce (cf. sections 6 et 6bis : le cas Saumos illustre exactement pourquoi).

## Saisonnalité

Le risque de churn hors saison est réel. Réponse : abonnement annuel justifié par la valeur permanente — historique, analyse d'exposition du portefeuille, fiches communes, préparation de saison. À intégrer au produit dès la conception, pas en rattrapage. (Les fiches communes statiques indexables y contribuent aussi côté acquisition, cf. 8.5.)

---

# 13. Exemples d'argumentaire commercial

## Pour une compagnie d'assurance
« Identifiez instantanément les biens assurés situés à proximité d'un incendie actif, suivez l'évolution commune par commune, et analysez hors saison l'exposition structurelle de votre portefeuille. »

## Pour un gestionnaire de réseau
« Surveillez automatiquement vos infrastructures : soyez informé lorsqu'un incendie détecté par satellite se rapproche de vos installations, avec le contexte météo et communal pour qualifier la situation. »

## Pour une commune
« En quelques secondes, visualisez la situation incendie de votre territoire et de ses environs, consultez l'historique et l'exposition de votre commune, sans consulter plusieurs sources. »

## Pour un camping
« Disposez d'une vision cartographique claire et continue des incendies suivis autour de votre établissement, avec leur évolution, la direction du vent et les communes concernées. »
*(Suivi et contexte, pas de promesse d'alerte précoce.)*

## Pour les médias
« Disposez d'une vision consolidée et cartographiée de l'évolution des incendies, commune par commune, à partir de données publiques enrichies et sourcées. »

**Matière de démonstration (nouveau) :** le déroulé du feu de Saumos reconstitué par le prototype (front actif, progression, intensité, communes) constitue un argumentaire clé en main : « voici ce que notre moteur produisait automatiquement pendant le feu, comparez avec ce dont vous disposiez. »

---

# 14. Positionnement marketing

Vigifeu ne vend pas des hotspots (déjà publics), ni de la détection (des acteurs capitalisés s'en chargent).

Vigifeu vend :

* une **traduction** : de la donnée satellitaire et météo vers une réponse métier (« suis-je concerné ? ») ;
* une **granularité communale** que personne ne fournit ;
* une veille opérationnelle et des notifications personnalisées sur les sites du client ;
* une rigueur de données de niveau contractuel ;
* un éditeur identifié et un cadre juridique clair.

---

# 15. Principes de responsabilité

Le message central reste : **« Vigifeu est un outil d'aide à la veille basé sur des données publiques. »**

Jamais « Vigifeu garantit... », « Vigifeu protège... », « Vigifeu prédit... » — et explicitement : jamais « zones menacées », jamais « propagation estimée », jamais « simulation de risque », **jamais « éteint »** (cf. section 4.1).

## Transparence de l'éditeur

L'observation du marché montre des acteurs diffusant de l'information de crise **anonymement, sans mentions légales** (infraction à la LCEN, et impossibilité d'engagement contractuel). A contrario, notre conformité est un **argument de vente** :

* mentions légales complètes (éditeur, directeur de publication, hébergeur) ;
* CGU, engagement de service, DPO, RGPD ;
* « Vous savez qui nous sommes, ce que nous garantissons et ce que nous ne garantissons pas. »

Une collectivité ou un assureur ne peut pas s'appuyer contractuellement sur un outil anonyme et gratuit, quelle que soit sa qualité technique.

## Honnêteté sur la latence

Le délai de détection est affiché, jamais masqué. Promettre l'instantanéité que la physique des satellites ne permet pas serait à la fois un risque juridique et un motif de résiliation. La latence est désormais **mesurée** (6bis) et le sera en continu : les délais affichés seront chiffrés, pas déclaratifs.

## 15bis. Page « Méthodologie & sources » (nouveau)

Page publique de premier rang (cf. arborescence 8.6), pas une annexe. Elle documente : les sources, leur fraîcheur et **leurs attributions exactes (registre 5.8 — citation NASA FIRMS avec disclaimer, mention IGN et millésimes, attribution Open-Meteo CC BY, EUMETSAT)**, la latence mesurée sur la saison en cours (« premier hotspot en moyenne N heures après le départ de feu »), les niveaux de confiance et leur signification, la sémantique exacte des libellés (« plus détecté » vs « éteint », donnée météo vs évaluation), et ce que l'outil **ne fait pas**. C'est la démonstration opérationnelle des principes ci-dessus — et vraisemblablement la première page qu'un juriste d'assureur lira avant de signer.

---

# 16. Exemple de Conditions Générales d'Utilisation (version de travail)

## Article 1 – Objet
Vigifeu fournit un service de consultation, d'agrégation et d'analyse de données publiques relatives aux incendies de végétation.

## Article 2 – Nature des données
Les informations diffusées proviennent de sources publiques nationales et internationales. Des délais, imprécisions ou indisponibilités peuvent exister. **En particulier, les détections satellitaires sont soumises à des délais structurels (fréquence de passage des satellites, traitement des données) pouvant atteindre plusieurs heures.**

## Article 3 – Finalité
Le service constitue un outil d'aide à la veille et à l'analyse. Il ne constitue pas un système officiel d'alerte, **ni un système d'alerte précoce, ni un outil de prévision de la propagation des incendies. Les représentations liées au vent constituent des données météorologiques factuelles et non une évaluation de menace. L'indication qu'une zone n'est plus détectée constitue une donnée d'observation satellitaire et ne signifie pas que le feu y est éteint.**

## Article 4 – Absence de garantie
Aucune décision opérationnelle ou de sécurité ne doit être prise sur la seule base des informations affichées. L'utilisateur demeure seul responsable de ses décisions.

## Article 5 – Sources officielles
En cas de risque immédiat, seules les informations diffusées par les autorités compétentes (services d'incendie, protection civile, autorités nationales ou locales) font foi.

## Article 6 – Disponibilité
Le service est fourni selon une obligation de moyens. Des interruptions ou indisponibilités peuvent intervenir.

## Article 7 – Limitation de responsabilité
La responsabilité de l'éditeur ne pourra être engagée en cas : d'erreur dans les données sources ; de retard de diffusion **inhérent aux sources ou aux délais satellitaires** ; d'absence de détection ; de fausse détection ; d'interprétation erronée par l'utilisateur ; de dommage direct ou indirect résultant de l'utilisation du service.

## Article 8 – Utilisation professionnelle
L'utilisateur reconnaît utiliser Vigifeu comme un outil complémentaire d'information. Il conserve l'entière responsabilité des mesures de prévention, d'évacuation ou de gestion de crise qu'il décide de mettre en œuvre.

## Article 9 – Données déclarées par l'utilisateur
Les sites, actifs et périmètres déclarés par l'utilisateur le sont sous sa responsabilité. L'exactitude de leur géolocalisation conditionne la pertinence des analyses et notifications.

---

# 17. Questions ouvertes (mises à jour)

**Répondues ou largement avancées depuis la v0.2 :**

* ~~Validation empirique de la latence réelle en Europe~~ → **mesurée sur le cas Saumos : 3–6 h d'acquisition + 1–3 h de traitement NRT, soit 5–9 h vécues (6bis).** Reste la distribution complète sur une saison : protocole de monitoring 15 min défini, à automatiser.
* ~~Faisabilité et coût d'accès au flux MTG-FCI~~ → **produit FIR L2 opérationnel, 10 min, Data Store EUMETSAT essentiellement gratuit (5.2).** Reste le test technique sur l'archive de Saumos.
* ~~Stratégie de qualification des faux positifs~~ → **taxonomie à trois signatures issue de l'observation (7.1)** ; reste la validation quantitative sur une saison complète et le croisement Corine/masques industriels.

**Toujours ouvertes :**

* Choix France seule vs France + Espagne au lancement (arbitrage effort de données / taille de marché) ;
* Modèle de prix par segment et structure de l'offre annuelle (valeur hors saison) ;
* Cadre d'un éventuel partenariat avec les acteurs associatifs (flux de signalements terrain comme source tierce).

**Nouvelles (issues de la validation) :**

* Paramètres du clustering spatio-temporel (fenêtres spatiale et temporelle, gestion des reprises et des fusions de feux) ;
* Validation de l'heuristique « trois signatures » sur une saison, taux de faux positifs/négatifs cible par segment ;
* Périmètre initial des fiches communes pré-générées (communes BDIFF + concernées vs France entière) ;
* ~~Conditions exactes d'usage et de republication du produit FIR MTG~~ → cadre identifié (5.8) : licence EUMETSAT « Service Provider » à obtenir formellement avant la phase 2 ;
* Stratégie de dé-duplication inter-satellites (par passage vs par pixel).

**Nouvelles (issues de la vérification des licences, 5.8) :**

* Open-Meteo au lancement commercial : abonnement payant vs bascule sur les sources primaires Météo-France (LO 2.0) ;
* Intégration de la base Prométhée (1973, arc méditerranéen) en complément de la BDIFF (2006, France entière) pour la profondeur historique des fiches communes ;
* Vérification résiduelle des conditions EFFIS/Copernicus (FWI) et Météo-France/VigiEau.

---

# 18. Conclusion

Le projet est passé, entre la v0.2 et la v0.3, du « techniquement réaliste » au **« techniquement validé »** : un prototype de quelques centaines de lignes a exécuté la chaîne complète sur données réelles, détecté et suivi les feux majeurs de juillet 2026, mesuré la latence, et produit des livrables (carte à cycle de vie, mesures de progression et d'intensité) qu'aucun outil du paysage n'offre sous cette forme.

Les quatre conclusions structurantes de la v0.2 sont confirmées et précisées :

1. **La latence satellitaire définit les segments** — désormais chiffrée (5–9 h vécues sur le cas de référence, trou d'observation de 12 h en phase de suivi) et bientôt compensée pour les feux significatifs par le flux géostationnaire MTG, dont l'accès s'avère plus simple que prévu.
2. **La commune est le chaînon manquant du marché** — démonstration par les faits : toute la communication de crise du feu de Saumos s'est faite commune par commune, et le rattachement doit être multi-communes par intersection de géométries.
3. **La barrière à l'entrée est la rigueur, pas la fonctionnalité** — le prototype le prouve dans les deux sens : les briques s'assemblent vite, et chaque raccourci (clustering purement spatial, rattachement au centroïde, heuristique trop étroite) produit exactement le type d'erreur qu'un client B2B ne pardonne pas.
4. **La conformité juridique est un argument commercial** — enrichie d'une sémantique précise (« plus détecté » vs « éteint », mesuré/déclaré/estimé/prévu) et d'une page méthodologie conçue comme un outil de vente.

Le moteur à construire se mesure toujours à trois critères — **qualifier** (trois signatures), **traduire** (langage métier), **rattacher** (communes et sites du client) — auxquels la validation ajoute un socle d'architecture arrêté : ingestion continue, SQLite + Parquet évoluant vers PostGIS, génération statique événementielle, historisation intégrale dès le premier jour. La prochaine étape est la spécification détaillée.
