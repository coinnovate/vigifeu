"""Import de l'historique des feux BDIFF → `commune_fire_history` (Spec 01 §5.3).

BDIFF (Base de Données sur les Incendies de Forêt en France, 2006+, France entière,
maille communale, Licence Ouverte 2.0) s'exporte en **CSV** depuis
bdiff.agriculture.gouv.fr. Prométhée (arc méditerranéen, 1973+) est reporté en v1.1
(hors périmètre Gironde de toute façon) — le champ `source_base` est prêt.

HYPOTHÈSE DE FORMAT (à vérifier contre l'export réel, comme les fetchers drought/vigieau) :
CSV à séparateur `;` ou `,`, en-têtes français. Colonnes consommées (tolérance sur
les libellés) : Année, Numéro, Code INSEE, Date de première alerte, Surface parcourue
(m²), Nature/Type de feu. La logique de mapping est isolée dans `_normalize_row` :
seule cette fonction est à ajuster si le format réel diffère.

Idempotence par **clé naturelle de feu** (`code_insee + année + date + numéro`), sans
contrainte de schéma : on n'insère que les feux pas déjà présents. Conséquences :
rejouer un fichier ne duplique jamais, et **plusieurs fichiers se cumulent** — utile
car l'export BDIFF est plafonné en taille (il faut souvent le découper). `replace=True`
efface d'abord tout l'historique BDIFF (repartir d'un export complet). Les codes INSEE
absents du référentiel `commune` sont ignorés et comptés (FK activées ; remap succession en v1.1).
"""

from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path
from typing import Iterator

SOURCE_BASE = "bdiff"


class BdiffImportError(Exception):
    pass


def _pick(raw: dict, *keys: str):
    for k in keys:
        for rk, v in raw.items():
            if rk and rk.strip().lower() == k.lower() and v not in (None, ""):
                return v
    return None


def _to_ha(surface_m2: str | None) -> float | None:
    if surface_m2 in (None, ""):
        return None
    val = str(surface_m2).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(val) / 10_000.0
    except ValueError:
        return None


_DMY = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


def _iso_date(d: str | None) -> str | None:
    if not d:
        return None
    d = d.strip()
    m = _DMY.match(d)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    # "AAAA-MM-JJ HH:MM:SS" (export BDIFF réel) → garder la date seule
    if len(d) >= 10 and d[4] == "-" and d[7] == "-":
        return d[:10]
    return d  # déjà ISO (ou format inconnu conservé tel quel)


def _normalize_row(raw: dict) -> dict | None:
    code = _pick(raw, "Code INSEE", "code_insee", "insee", "Code Insee")
    if not code:
        return None
    date_alerte = _iso_date(_pick(raw, "Date de première alerte", "date_alerte", "Date"))
    annee = _pick(raw, "Année", "annee", "Annee")
    if not annee and date_alerte and len(date_alerte) >= 4:
        annee = date_alerte[:4]
    return {
        "code_insee": str(code).strip(),
        "annee": int(annee) if annee not in (None, "") else None,
        "date_alerte": date_alerte,
        "surface_ha": _to_ha(_pick(raw, "Surface parcourue (m2)", "Surface parcourue (m²)",
                                   "surface_m2", "surface")),
        "type_feu": _pick(raw, "Nature", "Type de feu", "type_feu"),
        "source_ref": _pick(raw, "Numéro", "numero", "id"),
    }


def _read_csv(path: Path) -> Iterator[dict]:
    # BDIFF : encodage souvent cp1252 ; séparateur ; ou ,.
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise BdiffImportError(f"encodage illisible: {path}")
    lines = text.splitlines()
    # L'export BDIFF réel préfixe des lignes de préambule (avertissement, nombre de
    # résultats, critères de sélection) avant l'en-tête. On saute jusqu'à la première
    # ligne contenant « Code INSEE ». Sans préambule (CSV générique), rien n'est sauté.
    for i, ln in enumerate(lines):
        if "code insee" in ln.lower():
            lines = lines[i:]
            break
    sample = "\n".join(lines[:3])
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    yield from csv.DictReader(lines, delimiter=delim)


def _fire_key(r: dict) -> tuple:
    """Clé naturelle d'un feu BDIFF (dédup inter-fichiers et ré-import)."""
    return (r["code_insee"], r["annee"], r["date_alerte"], r["source_ref"])


def import_bdiff(conn: sqlite3.Connection, source: str | Path, *, replace: bool = False) -> dict:
    """Importe l'historique BDIFF depuis un CSV (cumulatif, dédup par feu).

    `replace=True` efface d'abord tout l'historique BDIFF. Retourne
    {imported, duplicates_ignored, skipped_unknown_commune, communes_touchees}.
    """
    path = Path(source)
    if not path.exists():
        raise BdiffImportError(f"source introuvable: {path}")

    known = {r["code_insee"] for r in conn.execute("SELECT code_insee FROM commune")}
    rows: list[dict] = []
    skipped = 0
    for raw in _read_csv(path):
        rec = _normalize_row(raw)
        if rec is None:
            continue
        if rec["code_insee"] not in known:
            skipped += 1
            continue
        rows.append(rec)

    if replace:
        conn.execute("DELETE FROM commune_fire_history WHERE source_base=?", (SOURCE_BASE,))
        seen: set[tuple] = set()
    else:
        seen = {
            _fire_key(r)
            for r in conn.execute(
                "SELECT code_insee, annee, date_alerte, source_ref "
                "FROM commune_fire_history WHERE source_base=?",
                (SOURCE_BASE,),
            )
        }

    to_insert: list[dict] = []
    duplicates = 0
    for r in rows:
        key = _fire_key(r)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        to_insert.append(r)

    conn.executemany(
        """INSERT INTO commune_fire_history
             (code_insee, annee, date_alerte, surface_ha, type_feu, source_base, source_ref)
           VALUES (:code_insee, :annee, :date_alerte, :surface_ha, :type_feu,
                   :source_base, :source_ref)""",
        [{**r, "source_base": SOURCE_BASE} for r in to_insert],
    )
    conn.commit()
    return {
        "imported": len(to_insert),
        "duplicates_ignored": duplicates,
        "skipped_unknown_commune": skipped,
        "communes_touchees": len({r["code_insee"] for r in rows}),
    }
