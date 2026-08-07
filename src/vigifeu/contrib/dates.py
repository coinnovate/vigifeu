"""Petits utilitaires de dates du canal contributif (ISO UTC, même format que la socle).

Centralisé ici pour que le dépôt (app.py), l'auto-filtre (filtre.py) et la purge (§9)
partagent exactement le même horodatage et le même calcul d'échéance (mois calendaires).
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta

_FMT = "%Y-%m-%dT%H:%M:%SZ"


def now_iso() -> str:
    """Horodatage serveur ISO UTC (`...Z`), aligné sur les timestamps de la socle."""
    return datetime.now(UTC).strftime(_FMT)


def parse_iso(iso: str) -> datetime:
    """ISO UTC (`...Z`) → datetime aware (UTC). Pour comparer des échéances (tokens, purge)."""
    return datetime.strptime(iso, _FMT).replace(tzinfo=UTC)


def plus_heures(iso: str, n: int) -> str:
    """`iso` + `n` heures (échéance des tokens d'action signés, §6)."""
    return (parse_iso(iso) + timedelta(hours=n)).strftime(_FMT)


def plus_mois(iso: str, n: int) -> str:
    """`iso` + `n` mois calendaires (jour ramené au dernier du mois si besoin). Pour les échéances de purge (§9)."""
    dt = datetime.strptime(iso, _FMT)
    total = dt.month - 1 + n
    an = dt.year + total // 12
    mois = total % 12 + 1
    jour = min(dt.day, calendar.monthrange(an, mois)[1])
    return dt.replace(year=an, month=mois, day=jour).strftime(_FMT)
