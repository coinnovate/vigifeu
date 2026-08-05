"""Fabrique de l'environnement Jinja2 (Spec 04 §2).

Les gabarits assemblent des **chaînes déjà produites par le lexique** (Spec 03 §2,
via `generate/feu.py` etc.) et n'y ajoutent que la structure HTML et les libellés de
sections. Autoescape actif : toute donnée injectée est échappée (défense XSS + HTML
valide). Aucun horodatage de génération n'est disponible dans l'environnement — la
seule heure affichable est celle de la donnée (Spec 03 P5 / Spec 04 §9.5).
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape


def make_env(
    templates_dir: str | Path,
    *,
    analytics: dict | None = None,
    pwa: dict | None = None,
) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,          # une variable oubliée = erreur, pas un trou silencieux
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    # Mesure d'audience (Umami, sans cookie) injectée dans toutes les pages via le gabarit
    # de base. Global (pas dans chaque contexte de page). Vide {} = aucun script émis.
    env.globals["analytics"] = analytics or {}
    # Métadonnées PWA (lien manifest, theme-color, enregistrement du service worker)
    # posées sur toutes les pages via le gabarit de base. Global, comme analytics : vide
    # {} = aucune balise PWA émise (le site reste un statique nginx ordinaire).
    env.globals["pwa"] = pwa or {}
    return env
