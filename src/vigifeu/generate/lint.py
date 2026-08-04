"""Garde-fous du site généré (Spec 04 §9) — le §4.1 du cadrage transformé en tests.

`lint_lexique` : grep des termes interdits sur l'intégralité du HTML généré ; un terme
interdit = build en échec (§9.1). `no_generation_timestamp` : aucune heure de génération
(§9.5). Ces fonctions sont appelées par la CI et, en avertissement, par `vigifeu generer`.
"""

from __future__ import annotations

import re
from pathlib import Path

from vigifeu.lexique.fr import TERMES_INTERDITS

# La page méthodologie est le GLOSSAIRE : elle cite légitimement les termes interdits
# pour les définir (« “plus détecté” n'est pas “éteint” »). Elle est donc exclue du lint.
EXCLUS_LINT = ("methodologie",)

# La section « Bulletins de veille presse » (Spec 09) est une lignée `declaree` ATTRIBUÉE,
# datée et marquée « à vérifier » : elle CITE la presse, ce n'est pas Vigifeu qui affirme.
# Un bulletin fidèle peut donc contenir des termes que le lexique s'interdit d'énoncer en son
# nom (« menacé », « hors de contrôle », « éteint »). On la retire avant le scan lexique
# (décision Spec 09 §0/§10). Pas de <section> imbriquée dedans → non-greedy sûr.
_SECTION_PRESSE = re.compile(r'<section class="bulletins">.*?</section>', re.DOTALL)


def texte_scannable(html: str) -> str:
    """HTML à soumettre au lint lexique, section presse attribuée retirée (Spec 09 §0/§10)."""
    return _SECTION_PRESSE.sub("", html)

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
        bas = texte_scannable(p.read_text(encoding="utf-8")).lower()
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
