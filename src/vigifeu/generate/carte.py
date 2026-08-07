"""Carte nationale — page d'accueil (Spec 04 §4, §8).

La carte MapLibre est un enrichissement ; la **liste des feux en cours** est le contenu
complet sans JS (§8) et le maillage vers les fiches. Le GeoJSON national est pré-généré
(`/feux.geojson`) et affiché par la carte, jamais construit côté client.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from jinja2 import Environment

from vigifeu.generate import jsonld, og
from vigifeu.lexique import fr


def _lieu(public_id: str) -> str:
    return public_id.partition("-")[2].replace("-", " ").title()


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def load_carte_context(conn: sqlite3.Connection, config: dict) -> dict:
    gen = config["generate"]
    gen_mtg = config.get("mtg", {}).get("activated", False)   # calque MTG seulement si activé (Spec 07 §)
    derniere_obs = conn.execute("SELECT MAX(acq_at) AS m FROM hotspot_raw").fetchone()["m"]
    # Seuil « nouveau » mesuré depuis la DERNIÈRE OBSERVATION (donnée), jamais l'heure de
    # génération (P0 : pas d'horodatage de build dans la sortie, Spec 04 §9).
    cutoff_nouveau = (
        _dt(derniere_obs) - timedelta(hours=gen.get("nouveau_max_h", 24))
        if derniere_obs else None
    )
    feux = []
    for r in conn.execute(
        "SELECT public_id, lifecycle, first_acq_at, last_acq_at, "
        "  (SELECT area_ha_estimee FROM fire_event_version v "
        "   WHERE v.fire_event_id = f.id ORDER BY version_n DESC LIMIT 1) AS area_ha "
        "FROM fire_event f "
        "WHERE public_id IS NOT NULL AND lifecycle <> 'archive' "
        "ORDER BY last_acq_at DESC"
    ):
        nouveau = bool(
            cutoff_nouveau and r["first_acq_at"] and _dt(r["first_acq_at"]) >= cutoff_nouveau
        )
        feux.append({
            "nom": f"Feu de {_lieu(r['public_id'])}",
            "url": f"/feux/{r['public_id']}/",
            "badge": fr.badge_cycle_de_vie(r["lifecycle"]),
            "classe": r["lifecycle"],
            "nouveau": fr.libelle_nouveau() if nouveau else None,
            "surface": fr.surface_estimee_courte(r["area_ha"]) if r["area_ha"] else None,
            "detecte_le": fr.horodatage(r["last_acq_at"]) if r["last_acq_at"] else None,
        })
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
        # Calque « signaux géostationnaires en attente » (Spec 07 §8) — UNIQUEMENT si MTG est activé.
        # Le 0682 étant abandonné (verdict §), `activated=false` ⇒ ni case, ni data-signals, ni fichier
        # (aucune UI fantôme). Revient tout seul si un produit MTG apte est rebranché (§13).
        "signaux_href": "/signaux.geojson" if gen_mtg else None,
        "signaux_toggle": fr.toggle_signaux() if gen_mtg else None,
        "latence_texte": fr.bloc_latence(derniere_obs) if derniere_obs else None,
        "attributions": fr.bloc_attributions(referentiel_millesime=gen["referentiel_millesime"]),
    }


def render_carte(env: Environment, ctx: dict) -> str:
    return env.get_template("carte.html.j2").render(**ctx)
