"""Pages liste départements (Spec 04 §4-5, plan §1.2 : listes simples).

Pages de liste légères qui donnent une structure de crawl et de navigation
(`Accueil → Département → commune`) : communes du périmètre du département + feux
suivis qui le touchent. Dérivées des données (générées en fin de build, pas via
regen_queue).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from jinja2 import Environment

from vigifeu.generate import jsonld, og
from vigifeu.generate.writer import write_atomic
from vigifeu.lexique import fr


def _lieu(public_id: str) -> str:
    return public_id.partition("-")[2].replace("-", " ").title()


def depts_du_perimetre(conn: sqlite3.Connection) -> list[str]:
    """Départements ayant au moins une commune du périmètre indexable (concernée/historique)."""
    return [r["dept"] for r in conn.execute(
        "SELECT DISTINCT c.dept FROM commune c WHERE c.dept IS NOT NULL AND ("
        "  c.code_insee IN (SELECT code_insee FROM fe_commune_rel) OR "
        "  c.code_insee IN (SELECT code_insee FROM commune_fire_history)) "
        "ORDER BY c.dept")]


def load_departement_context(conn: sqlite3.Connection, config: dict, dept: str) -> dict:
    gen = config["generate"]
    communes = [{"nom": r["nom"], "href": f"/communes/{r['code_insee']}-{r['slug']}/"}
                for r in conn.execute(
                    "SELECT code_insee, slug, nom FROM commune WHERE dept=? AND ("
                    "  code_insee IN (SELECT code_insee FROM fe_commune_rel) OR "
                    "  code_insee IN (SELECT code_insee FROM commune_fire_history)) "
                    "ORDER BY nom", (dept,))]
    feux = [{"nom": f"Feu de {_lieu(r['public_id'])}", "url": f"/feux/{r['public_id']}/"}
            for r in conn.execute(
                "SELECT DISTINCT f.public_id FROM fire_event f "
                "JOIN fe_commune_rel r ON r.fire_event_id = f.id "
                "JOIN commune c ON c.code_insee = r.code_insee "
                "WHERE c.dept=? AND f.public_id IS NOT NULL "
                "ORDER BY f.public_id", (dept,))]
    nom = fr.nom_departement(dept)
    canonical_path = f"/departements/{dept}/"
    place = {"@type": "AdministrativeArea", "@id": f"{gen['base_url']}{canonical_path}#place",
             "name": nom, "url": f"{gen['base_url']}{canonical_path}"}
    return {
        "base_url": gen["base_url"],
        "marque": gen["marque"],
        "canonical_path": canonical_path,
        "og_image": og.og_path(dept),
        "jsonld": jsonld.render_graph(jsonld.organization(gen["base_url"], gen["marque"]), place),
        "page_title": f"Incendies en {nom} ({dept}) — communes et feux suivis | {gen['marque']}",
        "page_description": f"Communes concernées et feux de végétation suivis en {nom} (département {dept}).",
        "fil_ariane": [{"label": "Accueil", "href": "/"},
                       {"label": "Départements", "href": "/departements/"},
                       {"label": nom, "href": None}],
        "dept": dept,
        "nom_dept": nom,
        "communes": communes,
        "feux": feux,
        "latence_texte": None,
        "attributions": [],
    }


def build_departements_index(conn: sqlite3.Connection, config: dict, env: Environment) -> int:
    """Page index `/departements/` : liste TOUS les départements du périmètre. Racine de
    crawl stable vers l'arbre communes, indépendante des feux actifs (navigation d'hiver)."""
    gen = config["generate"]
    depts = [{"code": d, "nom": fr.nom_departement(d), "href": f"/departements/{d}/"}
             for d in depts_du_perimetre(conn)]
    ctx = {
        "base_url": gen["base_url"], "marque": gen["marque"],
        "canonical_path": "/departements/",
        "og_image": og.og_path(None),
        "jsonld": jsonld.render_graph(jsonld.organization(gen["base_url"], gen["marque"])),
        "page_title": f"Incendies par département — carte des feux de végétation en France | {gen['marque']}",
        "page_description": "Parcourir les incendies de végétation par département : communes "
                            "concernées, feux suivis et historique, pour toute la France.",
        "fil_ariane": [{"label": "Accueil", "href": "/"}, {"label": "Départements", "href": None}],
        "departements": depts,
        "latence_texte": None, "attributions": [],
    }
    write_atomic(Path(gen["site_dir"]) / "departements" / "index.html",
                 env.get_template("departements-index.html.j2").render(**ctx))
    return len(depts)


def build_departements(conn: sqlite3.Connection, config: dict, env: Environment) -> int:
    site = Path(config["generate"]["site_dir"])
    tmpl = env.get_template("departement.html.j2")
    n = 0
    for dept in depts_du_perimetre(conn):
        ctx = load_departement_context(conn, config, dept)
        write_atomic(site / "departements" / dept / "index.html", tmpl.render(**ctx))
        n += 1
    build_departements_index(conn, config, env)   # + la page index /departements/
    return n
