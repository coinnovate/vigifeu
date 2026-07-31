# Vigifeu — Spécification 05 : Phase 2 (géostationnaire & couche commerciale)

**Version :** 0.1 (stub — cadrage à mener)
**Références :** cadrage v0.4 (§5.5, §5.8, §8.5, §8.6, §11, §15bis), Spec 01 (§3.8, §55, §147, §237),
Spec 02 (§4, §9, §33, §137, §189), Spec 03 (§3.3, §139, §154, §169), Spec 04 (§29, §38), plan-dev (Lot 6, §1.3)
**Périmètre :** tout ce qui suit le socle public (Lots 0-5, faits et déployés — `sentifeu.fr`).
Deux blocs indissociables : la **détection géostationnaire MTG** (technique) et la **couche
commerciale / SaaS** (monétisation).

**Statut :** brouillon de départ. Ce document **centralise** des éléments aujourd'hui éparpillés
dans le cadrage et les Specs 01-04, marqués « phase 2 / ultérieure / à terme ». Il reste à
**cadrer** (décisions explicites, priorités, jalons) avant de devenir une spéc exécutable. La
stratégie business (cible B2B, pricing, « MTG d'abord ou monétisation d'abord ») doit être posée
**avant** de figer la structure.

---

## 0. Principe de responsabilité (cadre tout le reste)

**Décision structurante (2026-07-31).** Les « alertes » évoquées dans les documents initiaux
deviennent des **notifications B2B contractualisées**, positionnées comme **aide à la décision**
pour des **professionnels** (gestionnaires forestiers, assureurs, exploitants de sites), **jamais**
comme un dispositif d'alerte grand public ni de sécurité des personnes.

Motif : la détection satellitaire est **structurellement faillible** (trous entre passages, nuages,
panache de fumée, feux sous le seuil de sensibilité, pannes de source). Fonder une **responsabilité
vie-humaine** sur ce signal est intenable — et détruirait le positionnement « **veille, pas
alerte** » qui fait la crédibilité du socle public.

Conséquences de conception :
- notifications **encadrées par contrat** (limitation de responsabilité), disclaimers lourds,
  assurance professionnelle, **jamais** positionnées comme sécurité ;
- l'**absence** de notification ne vaut jamais absence de feu ;
- les **POI** renforcent ce principe : un POI périmé (camping fermé, école déplacée) produit une
  mauvaise qualification → responsabilité. D'où l'exigence de **fraîcheur et de qualification**
  (cadrage §5.5), qui n'a de sens qu'en service pro.
- ⚠️ **Rectifier** le vocabulaire des anciens docs qui parlent d'« alertes aux clients »
  (cadrage §5.5 / §7bis, Spec 02 §5.2) à la lumière de ce principe.

---

## A. Détection géostationnaire (MTG) — le cœur technique

- `fetch_mtg_fir` toutes les 10 min, produit FIR EUMETSAT (Spec 02 §33).
- Table `geo_detection_raw` — sœur de `hotspot_raw`, confiance `probable`,
  `confirmed_by_fire_event_id` NULL (Spec 01 §147, réservée).
- Promotion `probable → confirmé` à la confirmation VIIRS, fenêtre **24 h / rayon 3 km** (Spec 02 §189).
- Affichage **carte nationale uniquement**, sans fiche ni `public_id`, libellé
  « Signal géostationnaire en attente de confirmation par satellite défilant » — jamais mêlé
  visuellement aux détections confirmées (Spec 03 §154, Spec 02 §137).
- Vague de régénération dédiée + attribution EUMETSAT dans les composants (Spec 04 §29, §38).
- Recalage des seuils de cellules contre VIIRS.
- **Licence EUMETSAT « Service Provider »** : démarche administrative = **chemin critique** de la
  phase 2, à lancer très en amont (plan §1.3, cadrage §5.8/§576).
- **Test technique FIR / Saumos** : valider le produit FIR sur la chronologie Saumos (22-25/07)
  contre le déroulé VIIRS connu (plan §177).

## B. Couche abonnés / SaaS (la monétisation)

- **Sites surveillés déclarés par l'abonné** — cœur du modèle économique (cadrage §15bis).
- **Notifications B2B** (voir §0) — dès `probable` sur les sites déclarés, au choix de l'abonné.
- **Espace abonné** : comptes, tableau de bord, portefeuilles ; couche dynamique servie hors du
  cache partagé (cadrage §8.5, §8.6).
- **API** (presse, assureurs, collectivités, gestionnaires forestiers).
- **Migration PostGIS** : à la signature du premier client multi-sites ou à la construction de
  l'espace abonné ; le schéma SQLite a été gardé propre pour ça (plan §1.3, cadrage §8.4).

## C. POI / enjeux

- Référentiel **POI** : OpenStreetMap (campings, écoles, hôpitaux, stations-service) complété par
  **BD TOPO** ; établissements sensibles, entreprises (cadrage §5.5, Spec 01 §55).
- **POI majeurs** sur la carte du feu (Spec 03 §3.3).
- Couplage **site déclaré ↔ POI ↔ feu détecté** : c'est ce qui *qualifie l'enjeu*
  (ex. « feu à 2 km d'un camping de 3 500 places » — cas Saumos/La Grigne, cadrage §6bis).
- ⚠️ Enjeu **fraîcheur / qualification** (cadrage §5.5) — voir §0.

## D. Enrichissements de fiche différés

- **Score d'exposition structurelle** : méthode à spécifier, affiché seulement une fois la méthode
  publiée en page méthodologie (Spec 01 §237, Spec 03 §4.5).
- **Vent au front** plutôt qu'au centroïde pour les grands feux (cadrage §5.4, Spec 01 §97).
- **Sources officielles** préfecture / SDIS dans les fiches : saisie manuelle en v1 ? ou flux
  semi-officiels en phase 2 (Spec 03 §169, cadrage §5.7).
- Partenariat associatif « basse confiance » comme source tierce (cadrage §11bis).

## E. Choix techniques reportés (v1.1 / au lancement)

- Image **Open Graph** rendue serveur (carte) → v1.1 (plan §1.2).
- **Open-Meteo payant vs Météo-France open data** : décision au lancement commercial (cadrage §5.6).
- Antenne de réception directe VIIRS en Europe : piste lointaine, hors MVP (cadrage §7bis).

---

## À trancher avant de rédiger la spéc exécutable

1. **MTG d'abord** (différenciateur temps réel) **ou monétisation d'abord** (sites déclarés +
   notifications B2B + API) ?
2. Cible B2B précise et modèle de prix.
3. Structure définitive de la Spec 05 (une seule spéc, ou séparer MTG / SaaS).
4. Calendrier EUMETSAT (chemin critique) — quand lancer la démarche.
