"""Garde-fous du site généré (Spec 04 §9) — le §4.1 du cadrage transformé en tests.

`lint_lexique` : grep des termes interdits sur l'intégralité du HTML généré ; un terme
interdit = build en échec (§9.1). `no_generation_timestamp` : aucune heure de génération
(§9.5). Ces fonctions sont appelées par la CI et, en avertissement, par `vigifeu generer`.
"""

from __future__ import annotations

from pathlib import Path

from vigifeu.lexique.fr import TERMES_INTERDITS

# La page méthodologie est le GLOSSAIRE : elle cite légitimement les termes interdits
# pour les définir (« “plus détecté” n'est pas “éteint” »). Elle est donc exclue du lint.
EXCLUS_LINT = ("methodologie",)

# Marqueurs d'un horodatage de génération (interdits §9.5) — seule l'heure de la DONNÉE
# (en heure locale de Paris) a le droit d'apparaître, jamais l'heure du build.
MARQUEURS_GENERATION = ("généré le", "generated on", "date de génération", "build time")


def _html_files(site_dir: str | Path):
    return Path(site_dir).rglob("*.html")


def lint_lexique(site_dir: str | Path) -> list[dict]:
    """Retourne la liste des violations {file, terme}. Vide = conforme (§9.1)."""
    violations = []
    for p in _html_files(site_dir):
        if any(x in p.parts for x in EXCLUS_LINT):
            continue
        bas = p.read_text(encoding="utf-8").lower()
        for terme in TERMES_INTERDITS:
            if terme.lower() in bas:
                violations.append({"file": str(p), "terme": terme})
    return violations


def no_generation_timestamp(site_dir: str | Path) -> list[dict]:
    """Retourne les pages portant un horodatage de génération (§9.5). Vide = conforme."""
    violations = []
    for p in _html_files(site_dir):
        bas = p.read_text(encoding="utf-8").lower()
        for marq in MARQUEURS_GENERATION:
            if marq in bas:
                violations.append({"file": str(p), "marqueur": marq})
    return violations
