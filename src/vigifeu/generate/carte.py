"""Carte nationale — page d'accueil (Spec 04 §4, §8).

La carte MapLibre est un enrichissement ; la **liste des feux en cours** est le contenu
complet sans JS (§8) et le maillage vers les fiches. Le GeoJSON national est pré-généré
(`/feux.geojson`) et affiché par la carte, jamais construit côté client.
"""

from __future__ import annotations

import sqlite3

from jinja2 import Environment

from vigifeu.generate import jsonld, og
from vigifeu.lexique import fr


def _lieu(public_id: str) -> str:
    return public_id.partition("-")[2].replace("-", " ").title()


def load_carte_context(conn: sqlite3.Connection, config: dict) -> dict:
    gen = config["generate"]
    derniere_obs = conn.execute("SELECT MAX(acq_at) AS m FROM hotspot_raw").fetchone()["m"]
    feux = [
        {
            "nom": f"Feu de {_lieu(r['public_id'])}",
            "url": f"/feux/{r['public_id']}/",
            "badge": fr.badge_cycle_de_vie(r["lifecycle"]),
            "classe": r["lifecycle"],
        }
        for r in conn.execute(
            "SELECT public_id, lifecycle FROM fire_event "
            "WHERE public_id IS NOT NULL AND lifecycle <> 'archive' "
            "ORDER BY last_acq_at DESC"
        )
    ]
    return {
        "base_url": gen["base_url"],
        "marque": gen["marque"],
        "canonical_path": "/",
        "og_image": og.og_path(None),
        "jsonld": jsonld.render_graph(jsonld.organization(gen["base_url"], gen["marque"])),
        "page_title": f"{gen['marque']} — carte des incendies de végétation en France",
        "page_description": (
            "Suivi satellite des incendies de végétation en France : carte des feux "
            "en cours, communes concernées, historique et contexte sécheresse."
        ),
        "fil_ariane": [{"label": "Accueil", "href": None}],
        "feux": feux,
        "geojson_href": "/feux.geojson",
        "latence_texte": fr.bloc_latence(derniere_obs) if derniere_obs else None,
        "attributions": fr.bloc_attributions(referentiel_millesime=gen["referentiel_millesime"]),
    }


def render_carte(env: Environment, ctx: dict) -> str:
    return env.get_template("carte.html.j2").render(**ctx)
