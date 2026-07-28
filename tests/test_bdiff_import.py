"""Import BDIFF → commune_fire_history (Lot 3, L3.2).

Fixture CSV synthétique (en-têtes BDIFF réels, séparateur `;`). Le référentiel
commune doit être importé d'abord (FK). Un code INSEE hors référentiel est ignoré
et compté (remap succession reporté v1.1).
"""

from __future__ import annotations

from pathlib import Path

from vigifeu.referentiels.bdiff import import_bdiff
from vigifeu.referentiels.communes import import_communes

COMMUNES = Path(__file__).parent / "fixtures" / "communes" / "gironde-ouest.geojson"
BDIFF = Path(__file__).parent / "fixtures" / "communes" / "bdiff-gironde-extrait.csv"
BDIFF_REEL = Path(__file__).parent / "fixtures" / "communes" / "bdiff-format-reel.csv"


def _with_communes(conn):
    import_communes(conn, COMMUNES, millesime="test-gironde")


def test_import_bdiff(db):
    conn, _ = db
    _with_communes(conn)
    res = import_bdiff(conn, BDIFF)
    # 5 lignes dont 1 sur une commune inconnue (99999) → 4 importées, 1 ignorée
    assert res["imported"] == 4
    assert res["skipped_unknown_commune"] == 1
    assert res["communes_touchees"] == 3  # Lacanau, Le Porge, Saumos


def test_conversion_et_normalisation(db):
    conn, _ = db
    _with_communes(conn)
    import_bdiff(conn, BDIFF)
    # Lacanau 2022 : 70 000 000 m² → 7000 ha ; date ISO ; source_base bdiff
    r = conn.execute(
        "SELECT * FROM commune_fire_history WHERE code_insee='33214' AND annee=2022"
    ).fetchone()
    assert r["surface_ha"] == 7000.0
    assert r["date_alerte"] == "2022-07-13"
    assert r["source_base"] == "bdiff"
    assert r["source_ref"] == "33-2022-0001"


def test_deux_feux_une_commune(db):
    """Saumos a deux entrées historiques (2019 et 2020)."""
    conn, _ = db
    _with_communes(conn)
    import_bdiff(conn, BDIFF)
    annees = {
        r["annee"]
        for r in conn.execute(
            "SELECT annee FROM commune_fire_history WHERE code_insee='33503'"
        )
    }
    assert annees == {2019, 2020}


def test_import_bdiff_format_reel(db):
    """Export BDIFF réel : lignes de préambule avant l'en-tête + dates horodatées."""
    conn, _ = db
    _with_communes(conn)
    res = import_bdiff(conn, BDIFF_REEL)
    # 3 lignes dont 99999 (inconnue) → 2 importées, préambule sauté (sinon 0)
    assert res["imported"] == 2
    assert res["skipped_unknown_commune"] == 1
    r = conn.execute(
        "SELECT surface_ha, date_alerte, type_feu FROM commune_fire_history WHERE code_insee='33214'"
    ).fetchone()
    assert r["surface_ha"] == 7000.0
    assert r["date_alerte"] == "2022-07-13"  # "2022-07-13 15:37:00" tronqué à la date
    assert r["type_feu"] == "Feu de forêt"


def test_import_bdiff_idempotent(db):
    """Rejouer le même fichier ne duplique pas (dédup par feu)."""
    conn, _ = db
    _with_communes(conn)
    import_bdiff(conn, BDIFF)
    res2 = import_bdiff(conn, BDIFF)
    assert res2["imported"] == 0
    assert res2["duplicates_ignored"] == 4
    assert conn.execute("SELECT COUNT(*) AS n FROM commune_fire_history").fetchone()["n"] == 4


def test_import_bdiff_cumulatif(db, tmp_path):
    """Deux fichiers (export plafonné) se cumulent : doublon exact ignoré, feu nouveau
    ajouté, et les feux du 1er fichier d'une commune partagée ne sont pas écrasés."""
    conn, _ = db
    _with_communes(conn)
    import_bdiff(conn, BDIFF)  # 4 feux dont Lacanau 2022 (numéro 33-2022-0001)
    f2 = tmp_path / "bdiff2.csv"
    f2.write_text(
        "Année;Numéro;Département;Code INSEE;Nom de la commune;"
        "Date de première alerte;Surface parcourue (m2);Nature\n"
        "2022;33-2022-0001;33;33214;Lacanau;13/07/2022;70000000;Feu de forêt\n"  # doublon exact
        "2021;33-2021-0007;33;33333;Le Porge;05/08/2021;90000;Feu de forêt\n",   # nouveau
        encoding="utf-8",
    )
    res = import_bdiff(conn, f2)
    assert res["imported"] == 1           # seul Le Porge 2021 est nouveau
    assert res["duplicates_ignored"] == 1  # Lacanau 2022 déjà présent (même clé)
    assert conn.execute("SELECT COUNT(*) AS n FROM commune_fire_history").fetchone()["n"] == 5
    # Saumos conserve ses deux feux du 1er fichier (aucun écrasement)
    annees = {r["annee"] for r in conn.execute(
        "SELECT annee FROM commune_fire_history WHERE code_insee='33503'")}
    assert annees == {2019, 2020}


def test_import_bdiff_replace(db):
    """--replace efface l'historique BDIFF avant de recharger."""
    conn, _ = db
    _with_communes(conn)
    import_bdiff(conn, BDIFF)  # 4 feux
    res = import_bdiff(conn, BDIFF_REEL, replace=True)  # efface puis charge 2
    assert res["imported"] == 2
    assert conn.execute("SELECT COUNT(*) AS n FROM commune_fire_history").fetchone()["n"] == 2
