"""Lexique contractuel français (Spec 03 §2).

Chaque fonction traduit une donnée du modèle (Spec 01) en une phrase **française
complète, datée et sourçable** (Spec 03 P1/P2). Règles transversales appliquées ici :

* **P4 — la catégorie de donnée est visible.** `mesuree` sans qualificatif ;
  `estimee` porte « estimation »/« estimé » ; `prevue` porte « Prévision {source} » ;
  `declaree` cite l'acte. Ces marqueurs sont dans les gabarits ci-dessous.
* **P5 — l'horodatage affiché est en heure locale française (Paris).** Les fonctions
  localisent l'instant de la donnée pour le grand public ; la donnée reste en UTC en base
  et dans le JSON-LD (citable par les machines). Les durées relatives (« il y a 3 h »)
  sont une surcouche JS côté client, hors lexique.
* **Interdits absolus** (cadrage §4.1, Spec 03 §2.3/§2.1) : aucune de ces fonctions
  ne produit « zone menacée », « propagation estimée », « sera touché », « éteint »,
  « maîtrisé » (hors citation `declaree`)… Le lint CI (Spec 04 §9.1) le vérifie sur
  le HTML généré ; `TERMES_INTERDITS` en est la source.

Les barèmes numériques (seuils DC/FWI/SIM) sont versionnés dans `config/params.toml`
`[lexique]` (tunables, Spec 03 §7.1) ; les **libellés** de classes, eux, sont
contractuels et vivent ici.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

# Heure AFFICHÉE = heure locale française (Europe/Paris) — les données restent en UTC
# en base et dans le JSON-LD (citable par les machines). Décision Lot 5 (accessibilité).
_PARIS = ZoneInfo("Europe/Paris")

# Termes proscrits (source unique du lint lexique, Spec 04 §9.1 / cadrage §4.1).
# « maîtrisé »/« fixé »/« éteint » ne sont admis QUE dans une citation `declaree`
# (« La préfecture indique… ») — le lint les tolère derrière l'attribut de citation.
TERMES_INTERDITS: tuple[str, ...] = (
    "éteint",
    "menacé",
    "menacée",
    "propagation estimée",
    "sera touché",
    "sera touchée",
    "hors de contrôle",
    "en voie d'extinction",
    "zone éteinte",
    "zone sécurisée",
    "front de flammes",
)

# Légende contractuelle du cône de vent (Spec 03 §2.3) — jamais reformulée.
LEGENDE_CONE = (
    "Direction actuelle du vent (donnée météorologique) — "
    "ne représente ni une prévision ni une zone de propagation"
)

# Noms officiels des départements (labels FR affichables → SEO « incendies en Gironde »
# plutôt que « département 33 »). Clé = code INSEE département (chaîne, avec 2A/2B/DROM).
NOMS_DEPARTEMENTS: dict[str, str] = {
    "01": "Ain", "02": "Aisne", "03": "Allier", "04": "Alpes-de-Haute-Provence",
    "05": "Hautes-Alpes", "06": "Alpes-Maritimes", "07": "Ardèche", "08": "Ardennes",
    "09": "Ariège", "10": "Aube", "11": "Aude", "12": "Aveyron", "13": "Bouches-du-Rhône",
    "14": "Calvados", "15": "Cantal", "16": "Charente", "17": "Charente-Maritime",
    "18": "Cher", "19": "Corrèze", "2A": "Corse-du-Sud", "2B": "Haute-Corse",
    "21": "Côte-d'Or", "22": "Côtes-d'Armor", "23": "Creuse", "24": "Dordogne",
    "25": "Doubs", "26": "Drôme", "27": "Eure", "28": "Eure-et-Loir", "29": "Finistère",
    "30": "Gard", "31": "Haute-Garonne", "32": "Gers", "33": "Gironde", "34": "Hérault",
    "35": "Ille-et-Vilaine", "36": "Indre", "37": "Indre-et-Loire", "38": "Isère",
    "39": "Jura", "40": "Landes", "41": "Loir-et-Cher", "42": "Loire", "43": "Haute-Loire",
    "44": "Loire-Atlantique", "45": "Loiret", "46": "Lot", "47": "Lot-et-Garonne",
    "48": "Lozère", "49": "Maine-et-Loire", "50": "Manche", "51": "Marne",
    "52": "Haute-Marne", "53": "Mayenne", "54": "Meurthe-et-Moselle", "55": "Meuse",
    "56": "Morbihan", "57": "Moselle", "58": "Nièvre", "59": "Nord", "60": "Oise",
    "61": "Orne", "62": "Pas-de-Calais", "63": "Puy-de-Dôme", "64": "Pyrénées-Atlantiques",
    "65": "Hautes-Pyrénées", "66": "Pyrénées-Orientales", "67": "Bas-Rhin", "68": "Haut-Rhin",
    "69": "Rhône", "70": "Haute-Saône", "71": "Saône-et-Loire", "72": "Sarthe",
    "73": "Savoie", "74": "Haute-Savoie", "75": "Paris", "76": "Seine-Maritime",
    "77": "Seine-et-Marne", "78": "Yvelines", "79": "Deux-Sèvres", "80": "Somme",
    "81": "Tarn", "82": "Tarn-et-Garonne", "83": "Var", "84": "Vaucluse", "85": "Vendée",
    "86": "Vienne", "87": "Haute-Vienne", "88": "Vosges", "89": "Yonne",
    "90": "Territoire de Belfort", "91": "Essonne", "92": "Hauts-de-Seine",
    "93": "Seine-Saint-Denis", "94": "Val-de-Marne", "95": "Val-d'Oise",
    "971": "Guadeloupe", "972": "Martinique", "973": "Guyane", "974": "La Réunion",
    "976": "Mayotte",
}


def nom_departement(code: str) -> str:
    """« Gironde » depuis « 33 ». Repli sur « département {code} » si code inconnu."""
    nom = NOMS_DEPARTEMENTS.get(code)
    return nom if nom else f"département {code}"


# --------------------------------------------------------------------------- #
# Helpers de formatage (dates UTC, nombres, directions, listes)               #
# --------------------------------------------------------------------------- #

def _parse(iso: str) -> datetime:
    """Parse un horodatage ISO (Z, +00:00, ou espace) en datetime UTC."""
    s = iso.strip().replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _local(iso: str) -> datetime:
    """Le même instant, exprimé en heure locale française (Europe/Paris)."""
    return _parse(iso).astimezone(_PARIS)


def date_fr(iso: str) -> str:
    """« 22/07/2026 » (jour local français)."""
    return _local(iso).strftime("%d/%m/%Y")


def heure_fr(iso: str) -> str:
    """« 14:32 » — heure locale française (Paris), sans suffixe."""
    return _local(iso).strftime("%H:%M")


def horodatage(iso: str) -> str:
    """« 22/07/2026 à 14:32 » — heure locale (Paris) ; l'unité d'horodatage des phrases.

    Les données restent en UTC en base et dans le JSON-LD ; seul l'affichage est localisé
    (Spec 03 P5 : l'heure affichée est celle de la donnée, désormais en heure française)."""
    return _local(iso).strftime("%d/%m/%Y à %H:%M")


def nombre_fr(x: float, decimals: int = 0) -> str:
    """Nombre à la française : virgule décimale, espace comme séparateur de milliers."""
    s = f"{float(x):,.{decimals}f}"          # ex. « 3,400.5 » (format US)
    return s.replace(",", " ").replace(".", ",")  # → « 3 400,5 »


_CARD16 = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO")
_CARD8_LONG = {
    "N": "le nord", "NE": "le nord-est", "E": "l'est", "SE": "le sud-est",
    "S": "le sud", "SO": "le sud-ouest", "O": "l'ouest", "NO": "le nord-ouest",
}


def cardinal_fr(deg: float, points: int = 16) -> str:
    """Abréviation cardinale (« OSO »). `points` = 8 ou 16."""
    if points == 8:
        idx = int((deg % 360) / 45 + 0.5) % 8
        return _CARD16[idx * 2]
    idx = int((deg % 360) / 22.5 + 0.5) % 16
    return _CARD16[idx]


def cardinal_long_fr(deg: float) -> str:
    """Direction cardinale en toutes lettres, 8 points (« le nord-est »)."""
    return _CARD8_LONG[cardinal_fr(deg, points=8)]


def _liste_fr(items) -> str:
    """« Le Porge, Lacanau et Lège-Cap-Ferret »."""
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} et {items[-1]}"


# --------------------------------------------------------------------------- #
# 2.1 — État et cycle de vie                                                  #
# --------------------------------------------------------------------------- #

_BADGE = {
    "actif": "Actif",
    "plus_detecte": "Plus détecté",
    "fusionne": "Fusionné",
    "archive": "Archivé",
}


def badge_cycle_de_vie(lifecycle: str) -> str:
    """Libellé court du badge d'en-tête (Spec 03 §3.1) — reflète le champ lifecycle."""
    return _BADGE[lifecycle]


def libelle_nouveau() -> str:
    """Étiquette « nouveau » (liste d'accueil) — feu détecté depuis moins de 24 h. Factuel,
    pas une alerte (le seuil est en config generate.nouveau_max_h)."""
    return "nouveau"


def surface_estimee_courte(ha: float) -> str:
    """Surface d'emprise, forme COURTE pour la liste d'accueil — catégorie `estimee`, jamais
    présentée comme un fait (« estimé »). La forme longue est phrase_emprise_estimee."""
    return f"≈ {nombre_fr(ha)} ha (estimé)"


def libelle_cycle_de_vie(
    lifecycle: str,
    *,
    detecte_dernier_passage: bool | None = None,
    heure_dernier_passage: str | None = None,
    heures_depuis: int | None = None,
    dernier_hotspot: str | None = None,
) -> str:
    """Phrase d'état (Spec 03 §2.1). `actif` et `plus_detecte` seulement.

    Pour `archive`, utiliser `bandeau_archive` ; pour `fusionne`, `mention_fusion`.
    """
    if lifecycle == "actif":
        if detecte_dernier_passage:
            return f"Détecté au dernier passage satellite ({heure_fr(heure_dernier_passage)})"
        return (
            f"Aucune détection au dernier passage ({heure_fr(heure_dernier_passage)}) "
            "— le suivi continue"
        )
    if lifecycle == "plus_detecte":
        return (
            f"Plus détecté depuis {heures_depuis} heures "
            f"(dernier point chaud : {horodatage(dernier_hotspot)})"
        )
    raise ValueError(
        f"libelle_cycle_de_vie ne couvre pas '{lifecycle}' "
        "(voir bandeau_archive / mention_fusion)"
    )


def bandeau_archive(derniere_detection: str) -> str:
    """Bandeau de fiche archivée (Spec 03 §3.11)."""
    return f"Feu archivé — dernière détection le {date_fr(derniere_detection)}"


def phrase_periode_suivi(first_acq: str, last_acq: str) -> str:
    """Période de suivi d'un feu (synthèse d'en-tête / mode archive, Spec 03 §3.2/§3.11)."""
    return f"Feu suivi du {date_fr(first_acq)} au {date_fr(last_acq)}"


_BADGE_CONFIANCE = {
    "confirme": "Détection confirmée",
    "probable": "Détection probable",
    "signalement": "Signalement",
}


def badge_confiance(niveau: str) -> str:
    """Libellé du badge de niveau de confiance (Spec 03 §3.1, cadrage §5.7)."""
    return _BADGE_CONFIANCE[niveau]


def libelle_zone_cellule(state: str, *, t_recent_h: int = 24) -> str:
    """Libellé d'une cellule selon son état d'ancienneté (Spec 03 §2.1)."""
    if state == "front_actif":
        return "Zone détectée au dernier passage"
    if state == "recent":
        return f"Zone détectée il y a moins de {t_recent_h} h"
    if state == "plus_detecte":
        return f"Zone plus détectée depuis plus de {t_recent_h} h"
    raise ValueError(f"état de cellule inconnu : {state}")


def mention_reprise(date: str) -> str:
    """`reprise=true` — jamais « reprise du feu » (terme SDIS), on ne l'affirme pas."""
    return (
        "Nouvelles détections dans une zone précédemment silencieuse "
        f"depuis le {date_fr(date)}"
    )


def mention_fusion(liens) -> str:
    """En-tête d'un feu issu d'une jonction (Spec 03 §3.1). `liens` = libellés des origines."""
    return f"Issu de la jonction de deux départs distincts ({_liste_fr(liens)})"


# --------------------------------------------------------------------------- #
# 2.2 — Mesures de dynamique                                                  #
# --------------------------------------------------------------------------- #

def phrase_progression(km: float, bearing_deg: float, *, passage_a: str, passage_b: str) -> str:
    """Progression du front entre deux passages comparables (Spec 03 §2.2)."""
    return (
        f"Le front de détection a progressé d'environ {nombre_fr(km, 1)} km "
        f"vers {cardinal_long_fr(bearing_deg)} entre le {horodatage(passage_a)} "
        f"et le {horodatage(passage_b)}"
    )


def phrase_frp(frp: float, *, type_passage: str, date: str) -> str:
    """Puissance thermique d'un passage (mesure FRP), sans comparaison (Spec 03 §2.2)."""
    return (
        f"Puissance thermique (FRP) : {nombre_fr(frp)} mégawatts "
        f"au passage de {type_passage} du {date_fr(date)}"
    )


def phrase_frp_comparee(
    frp: float, frp_precedent: float, *,
    type_courant: str, type_precedent: str, date: str,
) -> str:
    """FRP comparé au passage comparable précédent (Spec 03 §2.2).

    Garde-fou Spec 02 §6 / Spec 03 §2.2 : **jamais** de comparaison jour↔nuit
    (sensibilité du capteur). Le générateur refuse le gabarit si les types diffèrent.
    """
    if type_courant != type_precedent:
        raise ValueError(
            "comparaison FRP interdite entre passages de types différents "
            f"({type_precedent!r} vs {type_courant!r}) — Spec 03 §2.2"
        )
    if frp_precedent > 0:
        facteur = frp / frp_precedent
        comp = f"×{nombre_fr(facteur, 1)}" if facteur >= 1 else f"÷{nombre_fr(1 / facteur, 1)}"
    else:
        comp = "sans mesure comparable"
    return (
        f"Puissance thermique (FRP) : {nombre_fr(frp)} MW au passage de "
        f"{type_courant} du {date_fr(date)}, contre {nombre_fr(frp_precedent)} MW "
        f"au passage comparable précédent ({comp})"
    )


def phrase_emprise_estimee(ha: float) -> str:
    """Emprise satellite estimée — catégorie `estimee` (Spec 03 §2.2, P4)."""
    return (
        f"Emprise estimée d'après les détections : environ {nombre_fr(ha)} ha "
        "(estimation satellite, non officielle)"
    )


def phrase_surface_officielle(ha: float, autorite: str, date: str) -> str:
    """Surface parcourue annoncée par une autorité — catégorie `declaree` (Spec 03 §2.2)."""
    return f"Surface parcourue annoncée par {autorite} : {nombre_fr(ha)} ha ({date_fr(date)})"


# --------------------------------------------------------------------------- #
# 2.3 — Vent et direction                                                     #
# --------------------------------------------------------------------------- #

def phrase_vent(dir_origine_deg: float, v_kmh: float, rafales_kmh: float, *,
                provider: str, heure: str) -> str:
    """Observation de vent courante (Spec 03 §2.3). `dir_origine_deg` = d'où vient le vent."""
    return (
        f"Vent {cardinal_fr(dir_origine_deg, 16)} {nombre_fr(v_kmh)} km/h, "
        f"rafales {nombre_fr(rafales_kmh)} km/h — mesure {provider} de {heure_fr(heure)}"
    )


def phrase_meteo(*, temp_c=None, rh_pct=None, dir_origine_deg=None, v_kmh=None,
                 rafales_kmh=None, precip_1h=None, provider: str, heure: str) -> str | None:
    """Conditions météo observées au centroïde du foyer (Spec 03 §2.3) — factuelles,
    horodatées, sourcées. Les champs absents sont omis (source dégradée sans bloquer).
    None si aucune donnée exploitable."""
    parties: list[str] = []
    if temp_c is not None:
        parties.append(f"{nombre_fr(temp_c)} °C")
    if rh_pct is not None:
        parties.append(f"humidité {nombre_fr(rh_pct)} %")
    if dir_origine_deg is not None:
        vent = f"vent {cardinal_fr(dir_origine_deg, 16)} {nombre_fr(v_kmh or 0)} km/h"
        if rafales_kmh:
            vent += f" (rafales {nombre_fr(rafales_kmh)} km/h)"
        parties.append(vent)
    if precip_1h is not None:
        parties.append(f"{nombre_fr(precip_1h)} mm de pluie sur la dernière heure")
    if not parties:
        return None
    return f"Conditions au foyer (mesure {provider} de {heure_fr(heure)}) : {', '.join(parties)}"


def phrase_vent_communes(dir_origine_deg: float, communes, *, heure: str) -> str:
    """Fait composé vent + géométrie (Spec 03 §2.3). `communes` = noms dans l'aval du vent."""
    downwind = (dir_origine_deg + 180.0) % 360.0
    return (
        f"Le vent de {heure_fr(heure)} souffle en direction {cardinal_long_fr(downwind)} ; "
        f"dans cette direction se trouvent {_liste_fr(communes)}"
    )


# --------------------------------------------------------------------------- #
# 2.3bis — Enjeux à proximité (POI, Spec 06 §4) — public AGRÉGÉ, jamais nominatif #
# --------------------------------------------------------------------------- #

# Catégories v1 (Spec 05 §7) → libellé (singulier, pluriel). Ordre d'affichage fixe.
_ENJEU_LABELS = {
    "camping": ("camping", "campings"),
    "ecole": ("établissement scolaire", "établissements scolaires"),
    "hopital": ("hôpital", "hôpitaux"),
    "ehpad": ("EHPAD", "EHPAD"),
    "station_service": ("station-service", "stations-service"),
    "icpe_seveso": ("site Seveso", "sites Seveso"),
}


def _enjeu_item(category: str, n: int) -> str:
    sing, plur = _ENJEU_LABELS.get(category, (category, category))
    return f"{n} {sing if n == 1 else plur}"


def libelle_categorie_poi(category: str) -> str:
    """Libellé singulier d'une catégorie POI (infobulle de carte). Jamais de nom propre."""
    return _ENJEU_LABELS.get(category, (category, category))[0]


def categories_poi() -> list[str]:
    """Catégories POI dans l'ordre d'affichage du lexique (légende de carte, §4)."""
    return list(_ENJEU_LABELS)


def phrase_enjeux_poi(tier: str, counts: dict) -> str:
    """Énoncé AGRÉGÉ des enjeux d'un palier (Spec 06 §4). Jamais de nom ni de capacité.

    `tier` = 'emprise' (dans la zone détectée) ou 'proximite' (moins de 5 km). `counts` =
    {catégorie: nombre}. Chaîne vide si aucun enjeu. Aucun impact affirmé (P0) : « dans la
    zone détectée » est une observation de cellule, pas un constat de dégât."""
    items = [_enjeu_item(cat, counts[cat]) for cat in _ENJEU_LABELS if counts.get(cat)]
    if not items:
        return ""
    corps = _liste_fr(items)
    if tier == "emprise":
        return f"Dans la zone détectée du feu : {corps}"
    return f"À proximité (moins de 5 km) : {corps}"


def phrase_recensement_poi(counts: dict) -> str:
    """Recensement AGRÉGÉ des enjeux d'une commune (Spec 06 §4, fiche commune). Jamais nominatif.

    `counts` = {catégorie: nombre}. Chaîne vide si aucun enjeu recensé. Contenu permanent,
    hors événement (comme l'historique BDIFF) — pas une alerte."""
    items = [_enjeu_item(cat, counts[cat]) for cat in _ENJEU_LABELS if counts.get(cat)]
    if not items:
        return ""
    return f"Enjeux sensibles recensés dans la commune : {_liste_fr(items)}"


def note_enjeux_poi() -> str:
    """Réserve affichée sous les enjeux (Spec 06 §4 / P0) : ni impact, ni nominatif."""
    return ("Établissements sensibles recensés à partir de données publiques "
            "(OpenStreetMap, IGN) ; leur présence à proximité ne préjuge pas de dégâts — "
            "une zone détectée par satellite est une observation, pas un constat sur place.")


# --------------------------------------------------------------------------- #
# 2.4 — Prévisions météorologiques (catégorie `prevue`)                       #
# --------------------------------------------------------------------------- #

def phrase_prevision(provider: str, model: str, run_heure: str, contenu: str) -> str:
    """Gabarit unique de prévision (Spec 03 §2.4). Aucune conclusion opérationnelle dérivée."""
    return f"Prévision {provider}/{model} (run de {heure_fr(run_heure)}) : {contenu}"


def contenu_pluie(mm: float, heures: int, proba: int) -> str:
    """Contenu de prévision de pluie, à passer à `phrase_prevision`."""
    return f"{nombre_fr(mm)} mm de pluie attendus sur la zone d'ici {heures} h (probabilité {proba} %)"


# --------------------------------------------------------------------------- #
# 2.5 — Sécheresse et danger — barèmes de traduction                          #
# --------------------------------------------------------------------------- #

_DC_CLASSES = ("faible", "modérée", "élevée", "très élevée")
_FWI_CLASSES = ("très faible", "faible", "modéré", "élevé", "très élevé", "extrême")
_SIM_CLASSES = ("très inférieure", "inférieure", "proche", "supérieure")
_VIGIEAU_NIVEAUX = {
    "vigilance": "vigilance",
    "alerte": "alerte",
    "alerte_renforcee": "alerte renforcée",
    "crise": "crise",
}


def _classe(valeur: float, seuils, classes) -> str:
    """Range une valeur dans la classe dont l'indice est le nombre de seuils dépassés."""
    n = sum(1 for s in seuils if valeur >= s)
    return classes[n]


def classe_dc(dc: float, seuils) -> str:
    return _classe(dc, seuils, _DC_CLASSES)


def phrase_dc(dc: float, seuils) -> str:
    """Sécheresse profonde du terrain — indice DC (Spec 03 §2.5)."""
    return f"Sécheresse profonde du terrain : {classe_dc(dc, seuils)}"


def classe_fwi(fwi: float, seuils) -> str:
    return _classe(fwi, seuils, _FWI_CLASSES)


def phrase_fwi(fwi: float, date: str, seuils) -> str:
    """Danger météorologique d'incendie — indice FWI EFFIS (Spec 03 §2.5)."""
    return (
        f"Danger météorologique d'incendie ({date_fr(date)}) : "
        f"{classe_fwi(fwi, seuils)} (indice FWI, Copernicus/EFFIS)"
    )


def phrase_meteo_forets(niveau: str, dept: str, date: str) -> str:
    """Météo des forêts — classe officielle Météo-France, reprise telle quelle (Spec 03 §2.5)."""
    return f"Météo des forêts (Météo-France, {date_fr(date)}) : niveau {niveau} pour le département {dept}"


def classe_sim(percentile: float, seuils) -> str:
    return _classe(percentile, seuils, _SIM_CLASSES)


def phrase_sim(percentile: float, date: str, seuils) -> str:
    """Humidité des sols — percentile vs normale saisonnière, indice SIM (Spec 03 §2.5)."""
    return (
        f"Humidité des sols : {classe_sim(percentile, seuils)} à la normale de saison "
        f"(SIM, décade du {date_fr(date)})"
    )


def phrase_vigieau(niveau: str, date_arrete: str) -> str:
    """Restriction d'eau — citation de l'arrêté, catégorie `declaree` (Spec 03 §2.5)."""
    lib = _VIGIEAU_NIVEAUX[niveau]
    return f"Commune en {lib} sécheresse par arrêté préfectoral depuis le {date_fr(date_arrete)}"


# --------------------------------------------------------------------------- #
# Fiche commune — situation en cours (Spec 03 §4.2) et historique (§4.4)       #
# --------------------------------------------------------------------------- #

def commune_aucun_feu(derniere_obs: str, *, rayon_km: int = 20) -> str:
    """« Suis-je concerné ? » — aucun feu suivi (Spec 03 §4.2). Jamais « aucun risque »."""
    return (
        f"Aucun incendie suivi actuellement sur la commune ou à moins de {rayon_km} km "
        f"(dernière observation satellite : {horodatage(derniere_obs)})"
    )


def commune_relation_emprise(nom: str) -> str:
    """Relation active `emprise_dans_commune` (Spec 03 §4.2)."""
    return f"L'incendie de {nom} a une emprise sur la commune"


def commune_relation_distance(nom: str, km: float) -> str:
    """Relation active de proximité `a_moins_de_Nkm` (Spec 03 §4.2)."""
    return f"L'incendie de {nom} est suivi à {nombre_fr(km, 1)} km de la limite communale"


def commune_relation_vent(nom: str, heure: str) -> str:
    """Relation active `direction_vent` (Spec 03 §4.2) — fait composé, double horodatage."""
    return (
        f"La commune se trouve dans la direction actuelle du vent par rapport "
        f"à l'incendie de {nom} (vent de {heure_fr(heure)})"
    )


def commune_feu_suivi_intervalle(nom: str, valid_from: str, valid_to: str | None) -> str:
    """Feu Vigifeu ayant concerné la commune, relation ouverte ou fermée (Spec 03 §4.4)."""
    if valid_to is None:
        return f"Concernée par le feu de {nom} depuis le {date_fr(valid_from)}"
    return f"Concernée par le feu de {nom} du {date_fr(valid_from)} au {date_fr(valid_to)}"


def commune_historique_bdiff(n: int, surface_ha_totale: float | None, depuis: int) -> str:
    """Synthèse de l'historique BDIFF de la commune (Spec 03 §4.4)."""
    if n == 0:
        return f"Aucun incendie de végétation recensé depuis {depuis} (BDIFF)"
    surf = ""
    if surface_ha_totale:
        surf = f" — surface cumulée {nombre_fr(surface_ha_totale)} ha"
    return f"Depuis {depuis}, {n} incendie{'s' if n > 1 else ''} recensé{'s' if n > 1 else ''} (BDIFF){surf}"


def commune_exposition_foret(part_pct: float, surface_forestiere_ha: float) -> str:
    """Exposition structurelle — surface forestière et part du territoire (Spec 03 §4.5)."""
    return (
        f"Surface forestière : environ {nombre_fr(surface_forestiere_ha)} ha, "
        f"soit {nombre_fr(part_pct)} % du territoire communal"
    )


# --------------------------------------------------------------------------- #
# 2.6 — Latence et fraîcheur                                                  #
# --------------------------------------------------------------------------- #

def bloc_latence(derniere_obs: str) -> str:
    """Bloc standard de latence, sur chaque page (Spec 03 §2.6).

    Le mot « méthodologie » est transformé en lien par le gabarit (le lexique ne
    porte pas d'URL) ; la phrase reste exacte et lisible sans lien.
    """
    return (
        "Les détections satellitaires parviennent avec un délai de traitement de "
        "1 à 3 h après le passage ; un départ de feu peut précéder de plusieurs heures "
        "sa première détection (voir la méthodologie). "
        f"Dernière observation intégrée : {horodatage(derniere_obs)}."
    )


# --------------------------------------------------------------------------- #
# 2.7 — Attributions obligatoires                                             #
# --------------------------------------------------------------------------- #

def bloc_attributions(
    *,
    referentiel_millesime: str,
    meteo: str = "Open-Meteo (CC BY 4.0)",
    hotspots: bool = True,
    prometheus: bool = False,
) -> list[str]:
    """Lignes d'attribution de pied de page (Spec 03 §2.7).

    `hotspots` : citation NASA FIRMS obligatoire dès qu'un point chaud est affiché/exporté.
    Aucune formulation ne suggère un endossement par un producteur de données.
    """
    lignes: list[str] = []
    if hotspots:
        lignes.append("Détections : NASA FIRMS / LANCE / ESDIS (voir le disclaimer)")
    lignes.append(f"Limites administratives : IGN Admin Express, millésime {referentiel_millesime}")
    lignes.append(f"Météo : {meteo}")
    histo = "Historique incendies : BDIFF (min. Agriculture / IGN)"
    if prometheus:
        histo += ", Prométhée"
    lignes.append(histo)
    return lignes
