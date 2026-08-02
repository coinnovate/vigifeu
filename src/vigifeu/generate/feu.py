"""Fiche feu — assemblage du contexte et rendu (Spec 03 §3, Spec 04).

`load_fire_context` lit la base et produit un dict de **chaînes déjà traduites par le
lexique** (Spec 03 §2) + de listes structurées ; `render_feu` l'assemble en HTML.
Aucune chaîne libre : le fichier n'appelle que `lexique.fr` pour tout énoncé métier.
La page est une fonction pure de la base (Spec 04 P1) → golden file possible (§9.2).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from jinja2 import Environment
from shapely import wkt

from vigifeu.engine import version
from vigifeu.generate import jsonld, og
from vigifeu.generate.indicateurs import indicateurs_feu
from vigifeu.lexique import fr

_DN = {"D": "jour", "N": "nuit"}


# --------------------------------------------------------------------------- #
# Lectures                                                                    #
# --------------------------------------------------------------------------- #

def _fire_row(conn, event_id):
    row = conn.execute("SELECT * FROM fire_event WHERE id=?", (event_id,)).fetchone()
    if row is None:
        raise ValueError(f"fire_event {event_id} introuvable")
    return row


def _latest_version(conn, event_id):
    return conn.execute(
        "SELECT * FROM fire_event_version WHERE fire_event_id=? "
        "ORDER BY version_n DESC LIMIT 1",
        (event_id,),
    ).fetchone()


def _versions(conn, event_id):
    return conn.execute(
        "SELECT * FROM fire_event_version WHERE fire_event_id=? ORDER BY version_n DESC",
        (event_id,),
    ).fetchall()


def _latest_weather(conn, event_id):
    return conn.execute(
        "SELECT * FROM weather_obs WHERE fire_event_id=? "
        "ORDER BY observed_at DESC, id DESC LIMIT 1",
        (event_id,),
    ).fetchone()


def _relations(conn, event_id):
    """Relations feu ↔ commune, jointes au nom/slug/dept, groupées par type."""
    return conn.execute(
        "SELECT r.rel_type, r.distance_km, r.valid_from, r.valid_to, "
        "       c.code_insee, c.slug, c.nom, c.dept "
        "FROM fe_commune_rel r JOIN commune c ON c.code_insee = r.code_insee "
        "WHERE r.fire_event_id=? "
        "ORDER BY r.rel_type, r.distance_km IS NOT NULL, r.distance_km, c.nom",
        (event_id,),
    ).fetchall()


def _hotspot_count(conn, event_id):
    return conn.execute(
        "SELECT COUNT(*) AS n FROM hotspot_raw WHERE fire_event_id=?", (event_id,)
    ).fetchone()["n"]


def _poi_tiers(conn, event_id):
    """{palier: {category: n}} pour les paliers rendus sur la carte (emprise, < 5 km).

    Base commune aux phrases d'enjeux et à la légende : mêmes paliers que
    `geojson._poi_features`, pour que la légende décode exactement les marqueurs affichés."""
    rows = conn.execute(
        "SELECT r.rel_type, p.category, COUNT(*) AS n "
        "FROM fe_poi_rel r JOIN poi p ON p.id = r.poi_id "
        "WHERE r.fire_event_id=? AND r.valid_to IS NULL "
        "GROUP BY r.rel_type, p.category",
        (event_id,),
    ).fetchall()
    tiers: dict[str, dict] = {"emprise": {}, "a_moins_de_5km": {}}
    for r in rows:
        if r["rel_type"] in tiers:
            tiers[r["rel_type"]][r["category"]] = r["n"]
    return tiers


def _enjeux_poi(conn, event_id):
    """Enjeux POI AGRÉGÉS par palier (emprise, < 5 km), phrases via le lexique (Spec 06 §4).

    Public prudent : comptes par catégorie, jamais de nom ni de capacité. Les paliers
    10/20 km ne sont pas surfacés (faible signal comme enjeu ; ils restent en base)."""
    tiers = _poi_tiers(conn, event_id)
    phrases = []
    if tiers["emprise"]:
        phrases.append(fr.phrase_enjeux_poi("emprise", tiers["emprise"]))
    if tiers["a_moins_de_5km"]:
        phrases.append(fr.phrase_enjeux_poi("proximite", tiers["a_moins_de_5km"]))
    return phrases


def _enjeux_poi_legende(conn, event_id):
    """Clé de légende : catégories réellement présentes sur la carte (emprise + < 5 km),
    ordonnées comme les phrases (ordre du lexique). Décode les pastilles de couleur —
    sans elle, un point vert = camping n'est lisible qu'au survol (KO sur mobile)."""
    tiers = _poi_tiers(conn, event_id)
    present = set(tiers["emprise"]) | set(tiers["a_moins_de_5km"])
    return [
        {"category": cat, "libelle": fr.libelle_categorie_poi(cat)}
        for cat in fr.categories_poi()
        if cat in present
    ]


def _poi_referentiel_charge(conn):
    """Un référentiel POI est-il chargé ? Sinon la fiche reste en dégradé (pas d'assertion
    d'absence sans référentiel — Spec 03 P6, comme la sécheresse non armée)."""
    return conn.execute("SELECT 1 FROM poi LIMIT 1").fetchone() is not None


def _latest_progression(conn, event_id):
    """Progression au dernier passage vs le passage comparable précédent (§2.2)."""
    last = conn.execute(
        "SELECT o.id, o.window_start, o.day_night FROM overpass o "
        "JOIN hotspot_raw h ON h.overpass_id=o.id WHERE h.fire_event_id=? "
        "GROUP BY o.id ORDER BY o.window_start DESC LIMIT 1",
        (event_id,),
    ).fetchone()
    if last is None:
        return None
    fp = version.front_progress(conn, event_id, last["id"])
    if not fp["km"] or fp["bearing"] is None:
        return None
    prev = conn.execute(
        "SELECT o.window_start FROM overpass o JOIN hotspot_raw h ON h.overpass_id=o.id "
        "WHERE h.fire_event_id=? AND o.day_night=? AND o.window_start < ? "
        "GROUP BY o.id ORDER BY o.window_start DESC LIMIT 1",
        (event_id, last["day_night"], last["window_start"]),
    ).fetchone()
    if prev is None:
        return None
    return {"km": fp["km"], "bearing": fp["bearing"],
            "a": prev["window_start"], "b": last["window_start"]}


# --------------------------------------------------------------------------- #
# En-tête : nom et département dérivés                                         #
# --------------------------------------------------------------------------- #

def _nom_et_dept(public_id, relations):
    """« Feu de {lieu} ({dept}) » — lieu depuis le slug, dept depuis l'emprise."""
    annee, _, slug = (public_id or "").partition("-")
    lieu = slug.replace("-", " ").title() if slug else "sans nom"
    emprises = [r for r in relations if r["rel_type"] == "emprise_dans_commune"]
    dept = None
    if emprises:
        prefere = [r for r in emprises if r["slug"] == slug] or sorted(
            emprises, key=lambda r: r["code_insee"])
        dept = prefere[0]["dept"]
    return lieu, dept, annee


def _secheresse_meteo_forets(conn, config, dept):
    """Phrase du danger Météo des forêts pour le département du feu (dernier connu).

    None si drought non armé (le template affiche alors le mode dégradé) ou si aucune
    donnée n'est encore tombée pour ce département (template : « rien à signaler »)."""
    if not config["drought"]["activated"] or not dept:
        return None
    row = conn.execute(
        "SELECT value_class, valid_date FROM drought_obs "
        "WHERE indicator='meteo_forets' AND dept=? ORDER BY valid_date DESC LIMIT 1",
        (dept,),
    ).fetchone()
    if not row or not row["value_class"]:
        return None
    return fr.phrase_meteo_forets(row["value_class"], dept, row["valid_date"])


# --------------------------------------------------------------------------- #
# Assemblage du contexte                                                      #
# --------------------------------------------------------------------------- #

_REL_TITRES = {
    "emprise_dans_commune": "Emprise sur la commune",
    "a_moins_de_5km": "À moins de 5 km",
    "a_moins_de_10km": "À moins de 10 km",
    "a_moins_de_20km": "À moins de 20 km",
    "direction_vent": "Dans la direction actuelle du vent",
}
# Paliers proches (emprise, <5 km, direction du vent) affichés d'emblée ; les paliers
# lointains (<10/<20 km) sont repliés dans un <details> — les liens restent dans le
# HTML (maillage SEO, crawlables) mais la page reste scannable (décision Lot 4).
_REL_PRINCIPAUX = ["emprise_dans_commune", "a_moins_de_5km", "direction_vent"]
_REL_ELOIGNES = ["a_moins_de_10km", "a_moins_de_20km"]


def _groupes_communes(relations, rel_types):
    """Communes par type de relation, DÉDUPLIQUÉES par commune.

    `direction_vent` s'historise (une ligne par ouverture/fermeture au gré du vent) : la
    section « direction actuelle du vent » ne montre que les relations EN COURS (valid_to
    NULL) — pas l'historique. Pour les autres types, on fusionne les intervalles d'une même
    commune (relation en cours ⇒ pas d'intervalle ; sinon la fenêtre min→max).
    """
    groupes = []
    for rel_type in rel_types:
        rows = [r for r in relations if r["rel_type"] == rel_type]
        if rel_type == "direction_vent":
            rows = [r for r in rows if r["valid_to"] is None]   # « actuelle » = en cours
        par_commune: dict = {}
        for r in rows:
            c = par_commune.get(r["code_insee"])
            if c is None:
                par_commune[r["code_insee"]] = {
                    "nom": r["nom"], "code_insee": r["code_insee"], "slug": r["slug"],
                    "dist": r["distance_km"], "actif": r["valid_to"] is None,
                    "debut": r["valid_from"], "fin": r["valid_to"],
                }
                continue
            if r["valid_to"] is None:
                c["actif"] = True
            if r["valid_from"] and (c["debut"] is None or r["valid_from"] < c["debut"]):
                c["debut"] = r["valid_from"]
            if r["valid_to"] and (c["fin"] is None or r["valid_to"] > c["fin"]):
                c["fin"] = r["valid_to"]
        items = []
        for c in par_commune.values():
            interval = None
            if not c["actif"] and c["fin"] is not None:
                interval = f"concernée du {fr.date_fr(c['debut'])} au {fr.date_fr(c['fin'])}"
            items.append({"nom": c["nom"], "href": f"/communes/{c['code_insee']}-{c['slug']}/",
                          "interval": interval, "_dist": c["dist"]})
        items.sort(key=lambda x: (x["_dist"] is not None, x["_dist"] or 0, x["nom"]))
        if items:
            groupes.append({"titre": _REL_TITRES[rel_type], "communes": items})
    return groupes


def _synthese(conn, config, fire, latest, relations):
    """3 à 6 phrases générées (Spec 03 §3.2) — le paragraphe citable."""
    phrases = []
    lifecycle = fire["lifecycle"]
    if lifecycle == "archive":
        if fire["first_acq_at"] and fire["last_acq_at"]:
            phrases.append(fr.phrase_periode_suivi(fire["first_acq_at"], fire["last_acq_at"]) + ".")
    elif lifecycle == "plus_detecte" and fire["last_acq_at"]:
        # heures depuis la dernière détection : dérivé au rendu serait un horodatage
        # de génération (interdit §9.5) → on cite la date, le « depuis N h » est côté JS.
        phrases.append(
            f"Plus détecté depuis le {fr.date_fr(fire['last_acq_at'])} "
            f"(dernier hotspot : {fr.horodatage(fire['last_acq_at'])})."
        )
    elif lifecycle == "actif" and fire["last_acq_at"]:
        phrases.append(
            fr.libelle_cycle_de_vie("actif", detecte_dernier_passage=True,
                                    heure_dernier_passage=fire["last_acq_at"]) + "."
        )

    if latest and latest["area_ha_estimee"]:
        phrases.append(fr.phrase_emprise_estimee(latest["area_ha_estimee"]) + ".")

    prog = _latest_progression(conn, fire["id"])
    if prog:
        phrases.append(
            fr.phrase_progression(prog["km"], prog["bearing"],
                                  passage_a=prog["a"], passage_b=prog["b"]) + "."
        )

    series = version.intensity_series(conn, fire["id"], config)
    if series:
        dernier = series[-1]
        if dernier["frp"]:
            phrases.append(
                fr.phrase_frp(dernier["frp"], type_passage=_DN.get(dernier["dn"], "jour"),
                              date=dernier["at"]) + "."
            )

    wobs = _latest_weather(conn, fire["id"])
    if wobs and wobs["wind_dir_deg"] is not None:
        phrases.append(
            fr.phrase_vent(wobs["wind_dir_deg"], wobs["wind_speed_kmh"] or 0,
                           wobs["wind_gusts_kmh"] or 0, provider=wobs["provider"] or "météo",
                           heure=wobs["observed_at"]) + "."
        )
        aval = [r["nom"] for r in relations
                if r["rel_type"] == "direction_vent" and r["valid_to"] is None]
        if aval:
            phrases.append(
                fr.phrase_vent_communes(wobs["wind_dir_deg"], aval,
                                        heure=wobs["observed_at"]) + "."
            )
    return phrases


def _chronologie(conn, config, event_id):
    """Un jalon par passage avec détections (Spec 03 §3.5), ordre antichronologique.

    L'horodatage est celui du **passage** (donnée), jamais un stamp de traitement
    (§9.5). Jour et nuit distingués (§3.6) ; FRP dédupliqué inter-satellites (§6),
    non comparable jour↔nuit (note affichée dans le gabarit).
    """
    series = version.intensity_series(conn, event_id, config)
    lignes = [{
        "at": fr.horodatage(s["at"]),
        "type": _DN.get(s["dn"], "?"),
        "n_hotspots": s["n_dedup"],
        "frp": fr.nombre_fr(s["frp"]) if s["frp"] else None,
    } for s in series]
    lignes.reverse()   # antichronologique
    return lignes


def _imagerie_ctx(config: dict, fire) -> dict:
    """Champs imagerie Sentinel-2 (Spec 06 §5, cran 2) — POLITIQUE : n'afficher que s'il existe un
    passage S2 CLAIR DEPUIS le début du feu, avec sa VRAIE date (résolue côté client via le WFS,
    carte.js). Ici on prépare la fenêtre à interroger `[première détection, dernière + after]` + le
    seuil nuages + les gabarits de texte (la légende porte un `{date}` que carte.js remplit par la
    date réelle ; sinon message dégradé). La disponibilité (instance configurée) est décidée client.
    Un `rebuild` manuel n'a donc pas besoin de l'env (seul le daemon lit .env pour carte-config.js)."""
    if not fire["last_acq_at"]:
        return {"imagerie_from": None, "imagerie_to": None, "imagerie_maxcc": None,
                "imagerie_toggle": None, "imagerie_legende": None, "imagerie_indispo": None}
    img = config.get("imagerie", {})
    after = int(img.get("sentinelhub_after_days", 30))
    debut = fire["first_acq_at"]  # la fenêtre commence au DÉBUT du feu (post-feu seulement)
    debut_iso = debut[:10]
    fin_iso = (datetime.fromisoformat(fire["last_acq_at"].replace("Z", "+00:00"))
               + timedelta(days=after)).strftime("%Y-%m-%d")
    return {
        "imagerie_from": debut_iso,
        "imagerie_to": fin_iso,
        "imagerie_maxcc": int(img.get("sentinelhub_max_cloud_pct", 20)),
        "imagerie_toggle": fr.toggle_imagerie(),
        # {date} = marqueur remplacé par carte.js avec la date réelle du passage clair retenu.
        "imagerie_legende": fr.legende_imagerie_s2("{date}", img.get("sentinelhub_source", "")),
        "imagerie_indispo": fr.imagerie_indispo(fr.date_fr(debut)),
    }


def load_fire_context(conn: sqlite3.Connection, config: dict, event_id: int) -> dict:
    fire = _fire_row(conn, event_id)
    relations = _relations(conn, event_id)
    latest = _latest_version(conn, event_id)
    lieu, dept, annee = _nom_et_dept(fire["public_id"], relations)
    dept_txt = f" ({dept})" if dept else ""
    nom = f"Feu de {lieu}{dept_txt}"

    synthese = _synthese(conn, config, fire, latest, relations)
    wobs = _latest_weather(conn, event_id)

    # Bandeau d'indicateurs (résumé visuel en tête, cf. indicateurs.py).
    danger_foret = None
    if config["drought"]["activated"] and dept:
        _d = conn.execute(
            "SELECT value_class FROM drought_obs WHERE indicator='meteo_forets' AND dept=? "
            "ORDER BY valid_date DESC LIMIT 1", (dept,)).fetchone()
        danger_foret = _d["value_class"] if _d else None
    # « concernées » = directement dans/près du feu (emprise + < 5 km), pas les couronnes lointaines.
    n_concernees = len({r["code_insee"] for r in relations
                        if r["rel_type"] in ("emprise_dans_commune", "a_moins_de_5km")})
    indicateurs = indicateurs_feu(
        config, wobs=wobs, latest=latest, danger_foret=danger_foret,
        n_communes=n_concernees, actif=fire["lifecycle"] == "actif")

    gen = config["generate"]
    canonical_path = f"/feux/{fire['public_id']}/"
    description = synthese[0] if synthese else nom

    lat = lon = None
    if latest and latest["geometry_wkt"]:
        c = wkt.loads(latest["geometry_wkt"]).centroid
        lat, lon = c.y, c.x
    emprise = [{"nom": r["nom"], "href": f"/communes/{r['code_insee']}-{r['slug']}/"}
               for r in relations if r["rel_type"] == "emprise_dans_commune"][:8]
    graph = jsonld.render_graph(
        jsonld.organization(gen["base_url"], gen["marque"]),
        jsonld.feu_event(gen["base_url"], gen["marque"], nom=nom, url_path=canonical_path,
                         description=description, first_acq=fire["first_acq_at"],
                         last_acq=fire["last_acq_at"], lifecycle=fire["lifecycle"],
                         lat=lat, lon=lon, communes=emprise),
    )

    return {
        "base_url": gen["base_url"],
        "marque": gen["marque"],
        "canonical_path": canonical_path,
        "og_image": og.og_path(dept),
        "jsonld": graph,
        "indicateurs": indicateurs,
        "page_title": f"{nom} — suivi satellite, communes concernées | {gen['marque']}",
        "page_description": description,
        "fil_ariane": [
            {"label": "Accueil", "href": "/"},
            {"label": "Départements", "href": "/departements/"},
            *([{"label": fr.nom_departement(dept), "href": f"/departements/{dept}/"}] if dept else []),
            {"label": nom, "href": None},
        ],
        "nom": nom,
        "lieu": lieu,
        "annee": annee,
        "lifecycle": fire["lifecycle"],
        "badge_cycle": {"label": fr.badge_cycle_de_vie(fire["lifecycle"]), "classe": fire["lifecycle"]},
        "badge_confiance": fr.badge_confiance(fire["confidence_level"]) if fire["confidence_level"] else None,
        "first_acq": fr.horodatage(fire["first_acq_at"]) if fire["first_acq_at"] else None,
        "last_acq": fr.horodatage(fire["last_acq_at"]) if fire["last_acq_at"] else None,
        "bandeau_archive": (fr.bandeau_archive(fire["last_acq_at"])
                            if fire["lifecycle"] == "archive" and fire["last_acq_at"] else None),
        # Imagerie Sentinel-2 (Spec 06 §5, cran 2) : calque WMS daté sur la FENÊTRE du feu
        # (première détection → dernière + marge), le WMS renvoyant l'image mostRecent peu
        # nuageuse. Disponible seulement si une instance Sentinel Hub est configurée (env) —
        # sinon dégradé silencieux (pas de toggle mort). Discipline P0 : la légende dit « vue
        # la plus récente sur la période, l'étendue a pu évoluer » (date exacte côté serveur inconnue
        # avec mostRecent). **imagerie_from gate tout le bloc dans le gabarit.**
        **_imagerie_ctx(config, fire),
        "synthese": synthese,
        "communes_groupes": _groupes_communes(relations, _REL_PRINCIPAUX),
        "communes_groupes_eloignes": _groupes_communes(relations, _REL_ELOIGNES),
        # Enjeux POI (Spec 06 §4) : agrégé, jamais nominatif ; section absente tant qu'aucun
        # référentiel POI n'est chargé (dégradé, pas d'affirmation d'absence — P6).
        "enjeux_poi": _enjeux_poi(conn, event_id),
        "enjeux_poi_legende": _enjeux_poi_legende(conn, event_id),
        "enjeux_indispo": not _poi_referentiel_charge(conn),
        "enjeux_reserve": fr.note_enjeux_poi(),
        "chronologie": _chronologie(conn, config, event_id),
        # Contexte sécheresse : danger Météo des forêts du département (armé) ; dégradé
        # tant que [drought].activated est faux (Spec 03 P6 : l'absence est une info, pas
        # un trou silencieux) ; « rien à signaler » si armé mais sans donnée.
        "secheresse_indispo": not config["drought"]["activated"],
        "secheresse_meteo_forets": _secheresse_meteo_forets(conn, config, dept),
        "meteo_obs": (fr.phrase_meteo(
                          temp_c=wobs["temp_c"], rh_pct=wobs["rh_pct"],
                          dir_origine_deg=wobs["wind_dir_deg"], v_kmh=wobs["wind_speed_kmh"],
                          rafales_kmh=wobs["wind_gusts_kmh"], precip_1h=wobs["precip_mm_1h"],
                          provider=wobs["provider"] or "météo", heure=wobs["observed_at"])
                      if wobs else None),
        "brut": {
            "n_hotspots": _hotspot_count(conn, event_id),
            "n_versions": len(_versions(conn, event_id)),
            "geojson_href": f"{canonical_path}feu.geojson",
        },
        "latence_texte": fr.bloc_latence(fire["last_acq_at"] or fire["created_at"]),
        "attributions": fr.bloc_attributions(referentiel_millesime=gen["referentiel_millesime"]),
    }


def render_feu(env: Environment, ctx: dict) -> str:
    return env.get_template("feu.html.j2").render(**ctx)
