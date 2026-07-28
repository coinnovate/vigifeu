"""Lexique contractuel Vigifeu (Spec 03 §2, Spec 04 §2).

Les seules chaînes affichables du système. Le générateur (Lot 4) est un assembleur
de ces fonctions, jamais un rédacteur libre (Spec 03 P3). Une langue par module
(fr en v1) ; l'i18n (cadrage §3) ajoutera des modules frères sans casser l'assembleur.
"""

from __future__ import annotations

from vigifeu.lexique import fr

__all__ = ["fr"]
