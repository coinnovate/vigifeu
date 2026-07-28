"""JSON-LD schema.org (Spec 04 §6).

Les assistants génératifs et moteurs classiques citent les sources qui exposent des
**faits datés, sourcés, structurés**. Chaque page porte un `@graph` : l'éditeur
(`Organization`) + l'entité de la page (`Event` pour un feu, `Place`/`AdministrativeArea`
pour une commune). Généré depuis le modèle (Spec 01 §8), jamais saisi à la main.
"""

from __future__ import annotations

import json


def _to_iso(dt: str | None) -> str | None:
    """ISO 8601 avec suffixe Z (les timestamps du modèle sont en UTC, Spec 01 P7)."""
    if not dt:
        return None
    return dt.replace(" ", "T").replace("+00:00", "Z") if "Z" not in dt else dt


def organization(base_url: str, marque: str) -> dict:
    return {
        "@type": "Organization",
        "@id": f"{base_url}/#org",
        "name": marque,
        "url": f"{base_url}/",
        "description": "Veille satellitaire des incendies de végétation en France.",
    }


def feu_event(base_url: str, marque: str, *, nom: str, url_path: str, description: str,
              first_acq: str | None, last_acq: str | None, lifecycle: str,
              lat: float | None, lon: float | None, communes: list[dict]) -> dict:
    # eventStatus : un feu archivé/plus détecté n'est plus « en cours ».
    statut = ("https://schema.org/EventScheduled" if lifecycle == "actif"
              else "https://schema.org/EventScheduled")
    event = {
        "@type": "Event",
        "@id": f"{base_url}{url_path}#event",
        "name": nom,
        "description": description,
        "eventStatus": statut,
        "startDate": _to_iso(first_acq),
        "endDate": _to_iso(last_acq),
        "isAccessibleForFree": True,
        "about": [{"@type": "Place", "name": c["nom"], "url": f"{base_url}{c['href']}"}
                  for c in communes],
    }
    if lat is not None and lon is not None:
        event["location"] = {
            "@type": "Place", "name": nom,
            "geo": {"@type": "GeoCoordinates", "latitude": lat, "longitude": lon},
        }
    return {k: v for k, v in event.items() if v is not None}


def commune_place(base_url: str, *, nom: str, url_path: str, dept: str | None,
                  population: int | None, lat: float | None, lon: float | None,
                  depuis: str) -> dict:
    place = {
        "@type": "AdministrativeArea",
        "@id": f"{base_url}{url_path}#place",
        "name": nom,
        "url": f"{base_url}{url_path}",
        "description": f"Situation, historique et exposition aux incendies de végétation à {nom}{depuis}.",
    }
    if dept:
        place["containedInPlace"] = {"@type": "AdministrativeArea", "name": f"Département {dept}"}
    if population:
        place["additionalProperty"] = {"@type": "PropertyValue", "name": "population", "value": population}
    if lat is not None and lon is not None:
        place["geo"] = {"@type": "GeoCoordinates", "latitude": lat, "longitude": lon}
    return place


def faq_page(base_url: str, url_path: str, qa: list[tuple[str, str]]) -> dict:
    """FAQPage — définitions des libellés, exploitable par moteurs classiques et génératifs (§6)."""
    return {
        "@type": "FAQPage",
        "@id": f"{base_url}{url_path}#faq",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in qa
        ],
    }


def render_graph(*nodes: dict) -> str:
    """Sérialise un @graph JSON-LD, échappé pour insertion sûre dans <script>."""
    graph = {"@context": "https://schema.org", "@graph": [n for n in nodes if n]}
    blob = json.dumps(graph, ensure_ascii=False, separators=(",", ":"))
    return blob.replace("</", "<\\/")   # neutralise une éventuelle fermeture </script>
