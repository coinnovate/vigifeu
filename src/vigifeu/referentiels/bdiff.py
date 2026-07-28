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

Idempotence sans contrainte de schéma : à chaque import, on **remplace** les lignes
BDIFF des communes présentes dans le fichier (DELETE puis INSERT). Rejouer le même
fichier — ou ré-importer dept par dept — ne duplique jamais. Les codes INSEE absents
du référentiel `commune` sont ignorés et comptés (FK activées ; remap succession en v1.1).
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
    sample = text[:4096]
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    yield from csv.DictReader(text.splitlines(), delimiter=delim)


def import_bdiff(conn: sqlite3.Connection, source: str | Path) -> dict:
    """Importe/actualise commune_fire_history depuis un CSV BDIFF (idempotent).

    Retourne {imported, skipped_unknown_commune, communes_touchees}.
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

    touched = {r["code_insee"] for r in rows}
    # Idempotence : on remplace les lignes BDIFF des communes présentes dans le fichier.
    for code in touched:
        conn.execute(
            "DELETE FROM commune_fire_history WHERE source_base=? AND code_insee=?",
            (SOURCE_BASE, code),
        )
    conn.executemany(
        """INSERT INTO commune_fire_history
             (code_insee, annee, date_alerte, surface_ha, type_feu, source_base, source_ref)
           VALUES (:code_insee, :annee, :date_alerte, :surface_ha, :type_feu,
                   :source_base, :source_ref)""",
        [{**r, "source_base": SOURCE_BASE} for r in rows],
    )
    conn.commit()
    return {
        "imported": len(rows),
        "skipped_unknown_commune": skipped,
        "communes_touchees": len(touched),
    }
