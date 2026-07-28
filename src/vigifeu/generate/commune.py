"""Fiche commune — assemblage du contexte et rendu (Spec 03 §4, Spec 04).

Comme la fiche feu : lectures → chaînes du lexique → HTML (page = fonction pure des
données, P1). La fiche hors événement reste **complète** (§4.8) — c'est la valeur hors
saison et le maillage SEO (cadrage §8.5). Ton neutre : « aucune détection » ≠ « aucun
risque ». Le contexte sécheresse est en **mode dégradé** tant que la source n'est pas
armée (`[drought].activated=false`, Spec 03 P6).
"""

from __future__ import annotations

import sqlite3

from jinja2 import Environment

from vigifeu.generate import jsonld, og
from vigifeu.lexique import fr


def _fire_lieu(public_id: str) -> str:
    """« 2026-saumos » → « Saumos » (lieu principal, cohérent avec la fiche feu)."""
    _, _, slug = (public_id or "").partition("-")
    return slug.replace("-", " ").title() if slug else "sans nom"


def _commune_row(conn, code_insee):
    row = conn.execute("SELECT * FROM commune WHERE code_insee=?", (code_insee,)).fetchone()
    if row is None:
        raise ValueError(f"commune {code_insee} introuvable")
    return row


def _derniere_obs(conn):
    row = conn.execute("SELECT MAX(acq_at) AS m FROM hotspot_raw").fetchone()
    return row["m"] if row else None


def _situation(conn, code_insee, derniere_obs):
    """Situation en cours (§4.2) : relations actives vers des feux non archivés."""
    rows = conn.execute(
        "SELECT r.rel_type, r.distance_km, f.public_id, f.lifecycle "
        "FROM fe_commune_rel r JOIN fire_event f ON f.id = r.fire_event_id "
        "WHERE r.code_insee=? AND r.valid_to IS NULL "
        "AND f.public_id IS NOT NULL AND f.lifecycle <> 'archive' "
        "ORDER BY r.rel_type",
        (code_insee,),
    ).fetchall()
    items = []
    for r in rows:
        lieu = _fire_lieu(r["public_id"])
        href = f"/feux/{r['public_id']}/"
        if r["rel_type"] == "emprise_dans_commune":
            phrase = fr.commune_relation_emprise(lieu)
        elif r["rel_type"] == "direction_vent":
            phrase = fr.commune_relation_vent(lieu, derniere_obs) if derniere_obs else None
        elif r["distance_km"] is not None:
            phrase = fr.commune_relation_distance(lieu, r["distance_km"])
        else:
            phrase = None
        if phrase:
            items.append({"phrase": phrase, "href": href})
    return items


def _contexte_du_jour(conn, config, commune):
    """Contexte du jour (§4.3) : VigiEau (armé) + sécheresse (dégradée)."""
    ctx = {"vigieau": None, "secheresse_indispo": not config["drought"]["activated"],
           "meteo_forets": None, "fwi": None}
    arrete = conn.execute(
        "SELECT niveau, date_debut FROM vigieau_arrete "
        "WHERE code_insee=? AND date_fin IS NULL ORDER BY date_debut DESC LIMIT 1",
        (commune["code_insee"],),
    ).fetchone()
    if arrete:
        ctx["vigieau"] = fr.phrase_vigieau(arrete["niveau"], arrete["date_debut"])
    if config["drought"]["activated"]:
        seuils = config["lexique"]
        mf = conn.execute(
            "SELECT value_class, valid_date FROM drought_obs "
            "WHERE indicator='meteo_forets' AND dept=? ORDER BY valid_date DESC LIMIT 1",
            (commune["dept"],),
        ).fetchone()
        if mf and mf["value_class"]:
            ctx["meteo_forets"] = fr.phrase_meteo_forets(mf["value_class"], commune["dept"], mf["valid_date"])
        fwi = conn.execute(
            "SELECT value, valid_date FROM drought_obs "
            "WHERE indicator='fwi' AND code_insee=? ORDER BY valid_date DESC LIMIT 1",
            (commune["code_insee"],),
        ).fetchone()
        if fwi and fwi["value"] is not None:
            ctx["fwi"] = fr.phrase_fwi(fwi["value"], fwi["valid_date"], seuils["fwi_seuils"])
    return ctx


def _historique(conn, code_insee):
    """Historique incendies (§4.4) : synthèse BDIFF + événements notables + feux Vigifeu."""
    agg = conn.execute(
        "SELECT COUNT(*) AS n, SUM(surface_ha) AS s, MIN(annee) AS depuis "
        "FROM commune_fire_history WHERE code_insee=? AND source_base='bdiff'",
        (code_insee,),
    ).fetchone()
    depuis = agg["depuis"] or 2006
    synthese = fr.commune_historique_bdiff(agg["n"], agg["s"], depuis)
    notables = [
        {"annee": r["annee"], "surface": fr.nombre_fr(r["surface_ha"]) if r["surface_ha"] else None,
         "type": r["type_feu"]}
        for r in conn.execute(
            "SELECT annee, surface_ha, type_feu FROM commune_fire_history "
            "WHERE code_insee=? AND source_base='bdiff' AND surface_ha IS NOT NULL "
            "ORDER BY surface_ha DESC LIMIT 5",
            (code_insee,),
        )
    ]
    # Feux suivis par Vigifeu (relations ouvertes ou fermées), un par feu publié.
    suivis = []
    for r in conn.execute(
        "SELECT f.public_id, MIN(r.valid_from) AS vf, "
        "  CASE WHEN SUM(CASE WHEN r.valid_to IS NULL THEN 1 ELSE 0 END) > 0 "
        "       THEN NULL ELSE MAX(r.valid_to) END AS vt "
        "FROM fe_commune_rel r JOIN fire_event f ON f.id = r.fire_event_id "
        "WHERE r.code_insee=? AND f.public_id IS NOT NULL "
        "GROUP BY f.public_id ORDER BY vf DESC",
        (code_insee,),
    ):
        lieu = _fire_lieu(r["public_id"])
        suivis.append({
            "phrase": fr.commune_feu_suivi_intervalle(lieu, r["vf"], r["vt"]),
            "href": f"/feux/{r['public_id']}/",
        })
    return {"synthese": synthese, "notables": notables, "suivis": suivis}


def load_commune_context(conn: sqlite3.Connection, config: dict, code_insee: str) -> dict:
    commune = _commune_row(conn, code_insee)
    gen = config["generate"]
    derniere_obs = _derniere_obs(conn)
    situation = _situation(conn, code_insee, derniere_obs)
    seg = f"{code_insee}-{commune['slug']}"
    canonical_path = f"/communes/{seg}/"
    dept = commune["dept"]
    nom_dept = f"{commune['nom']} ({dept})" if dept else commune["nom"]

    exposition = None
    if commune["surface_forestiere_ha"] and commune["surface_ha"]:
        part = 100.0 * commune["surface_forestiere_ha"] / commune["surface_ha"]
        exposition = fr.commune_exposition_foret(part, commune["surface_forestiere_ha"])

    reglementaire = []
    if commune["pprif"]:
        reglementaire.append(f"PPRIF : {commune['pprif']}")
    if commune["obligation_debroussaillement"]:
        reglementaire.append("Obligations légales de débroussaillement en vigueur")

    fil = [{"label": "Accueil", "href": "/"}]
    if dept:
        fil.append({"label": f"Département {dept}", "href": f"/departements/{dept}/"})
    fil.append({"label": commune["nom"], "href": None})

    # « depuis 1973 » réservé aux communes Prométhée (arc méditerranéen) ; « depuis 2006 »
    # sinon (Spec 04 §5, Spec 01 §5.3). Prométhée non importé en v1 → 2006 partout.
    depuis = " depuis 2006"
    graph = jsonld.render_graph(
        jsonld.organization(gen["base_url"], gen["marque"]),
        jsonld.commune_place(gen["base_url"], nom=commune["nom"], url_path=canonical_path,
                             dept=dept, population=commune["population"],
                             lat=commune["centroid_lat"], lon=commune["centroid_lon"],
                             depuis=depuis),
    )

    return {
        "base_url": gen["base_url"],
        "marque": gen["marque"],
        "canonical_path": canonical_path,
        "og_image": og.og_path(dept),
        "jsonld": graph,
        "url_seg": seg,
        "page_title": f"Incendies à {commune['nom']} ({dept}) — situation, historique, exposition | {gen['marque']}",
        "page_description": (situation[0]["phrase"] if situation
                             else fr.commune_aucun_feu(derniere_obs) if derniere_obs
                             else f"Situation incendies à {commune['nom']}"),
        "fil_ariane": fil,
        "nom": commune["nom"],
        "nom_dept": nom_dept,
        "dept": dept,
        "epci": commune["epci_code"],
        "population": commune["population"],
        "millesime": commune["referentiel_millesime"],
        "situation": situation,
        "aucun_feu": (fr.commune_aucun_feu(derniere_obs) if not situation and derniere_obs else None),
        "contexte": _contexte_du_jour(conn, config, commune),
        "historique": _historique(conn, code_insee),
        "exposition": exposition,
        "reglementaire": reglementaire,
        "latence_texte": fr.bloc_latence(derniere_obs) if derniere_obs else None,
        "attributions": fr.bloc_attributions(referentiel_millesime=gen["referentiel_millesime"],
                                             hotspots=bool(situation)),
    }


def render_commune(env: Environment, ctx: dict) -> str:
    return env.get_template("commune.html.j2").render(**ctx)
