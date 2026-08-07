"""Tests de l'auto-filtre (Spec 10 §5, étape 5) — verdict pur + worker, sans modèle.

La règle de verdict et le worker `filtrer_lot` sont testés avec un **faux classifieur**
(le vrai moteur ONNX vit dans filtre_onnx.py, vérifié live). Fixtures d'images → verdict
(`nsfw`/`hors_sujet`/`ok`), transitions de statut, échéance de purge, et résilience
(inférence qui lève → la contribution reste `soumise`).
"""

from __future__ import annotations

import copy
import hashlib
from io import BytesIO

import pytest
from PIL import Image

from vigifeu.contrib.dates import plus_mois
from vigifeu.contrib.db import connect_contrib, migrate_contrib
from vigifeu.contrib.filtre import Scores, filtrer_lot, statut_pour_verdict, verdict
from vigifeu.model.db import load_config


# --- verdict (unitaire) ---------------------------------------------------

def test_verdict_nsfw_prioritaire():
    # NSFW ≥ seuil l'emporte même si le score feu est bon.
    assert verdict(0.9, 0.9, seuil_nsfw=0.85, seuil_feu=0.30) == "nsfw"


def test_verdict_hors_sujet():
    assert verdict(0.1, 0.2, seuil_nsfw=0.85, seuil_feu=0.30) == "hors_sujet"


def test_verdict_ok():
    assert verdict(0.1, 0.8, seuil_nsfw=0.85, seuil_feu=0.30) == "ok"


def test_statut_pour_verdict():
    assert statut_pour_verdict("ok") == "a_moderer"
    assert statut_pour_verdict("nsfw") == "auto_rejetee"
    assert statut_pour_verdict("hors_sujet") == "auto_rejetee"


# --- worker filtrer_lot ---------------------------------------------------

def _img(taille: int, couleur=(120, 120, 120)) -> bytes:
    tampon = BytesIO()
    Image.new("RGB", (taille, taille), couleur).save(tampon, format="JPEG")
    return tampon.getvalue()


class FauxClassifieur:
    """Mappe une fixture image → Scores par sa largeur (fixture image → verdict)."""

    def __init__(self, par_largeur: dict[int, Scores], lever_sur: set[int] | None = None):
        self.par_largeur = par_largeur
        self.lever_sur = lever_sur or set()
        self.appels = 0

    def classer(self, raw: bytes) -> Scores:
        self.appels += 1
        with Image.open(BytesIO(raw)) as im:
            w = im.width
        if w in self.lever_sur:
            raise RuntimeError("inférence en échec (timeout simulé)")
        return self.par_largeur[w]


@pytest.fixture()
def cc(tmp_path):
    conn = connect_contrib(str(tmp_path / "contributions.db"))
    migrate_contrib(conn)
    yield conn
    conn.close()


def _ajout_soumise(cc, tmp_path, img: bytes) -> int:
    """Écrit l'image et insère une contribution `soumise` minimale. Retourne son id."""
    sha = hashlib.sha256(img).hexdigest()
    chemin = tmp_path / f"{sha}.jpg"
    chemin.write_bytes(img)
    cur = cc.execute(
        "INSERT INTO contribution (captured_at, image_path, image_sha256, consentement_at, "
        "cgu_version, statut, created_at) VALUES (?,?,?,?,?, 'soumise', ?)",
        ("2026-08-07T10:00:00Z", str(chemin), sha, "2026-08-07T10:00:00Z",
         "2026-08", "2026-08-07T10:00:00Z"),
    )
    cc.commit()
    return cur.lastrowid


@pytest.fixture()
def config():
    return copy.deepcopy(load_config("config/params.toml"))


def test_trois_verdicts_et_transitions(cc, tmp_path, config):
    """Trois fixtures → nsfw/hors_sujet/ok → auto_rejetee/auto_rejetee/a_moderer."""
    id_nsfw = _ajout_soumise(cc, tmp_path, _img(100))
    id_hors = _ajout_soumise(cc, tmp_path, _img(200))
    id_ok = _ajout_soumise(cc, tmp_path, _img(300))

    faux = FauxClassifieur({
        100: Scores(0.95, 0.10, "faux"),   # nsfw
        200: Scores(0.05, 0.10, "faux"),   # hors_sujet
        300: Scores(0.05, 0.80, "faux"),   # ok
    })
    res = filtrer_lot(cc, config, faux)

    assert res["vues"] == 3 and res["traitees"] == 3
    assert res["a_moderer"] == 1 and res["auto_rejetee"] == 2 and res["erreurs"] == 0
    assert res["a_moderer_ids"] == [id_ok]  # l'unique passée en file humaine
    statuts = {
        r["id"]: r["statut"]
        for r in cc.execute("SELECT id, statut FROM contribution").fetchall()
    }
    assert statuts[id_nsfw] == "auto_rejetee"
    assert statuts[id_hors] == "auto_rejetee"
    assert statuts[id_ok] == "a_moderer"


def test_scores_et_moteur_enregistres(cc, tmp_path, config):
    _ajout_soumise(cc, tmp_path, _img(300))
    faux = FauxClassifieur({300: Scores(0.05, 0.80, "nudenet:x;clip:y", {"sim": 0.8})})
    filtrer_lot(cc, config, faux)

    r = cc.execute(
        "SELECT score_nsfw, score_feu, auto_verdict, auto_json, moteur_auto FROM contribution"
    ).fetchone()
    assert r["score_feu"] == 0.80
    assert r["auto_verdict"] == "ok"
    assert r["moteur_auto"] == "nudenet:x;clip:y"
    assert "sim" in r["auto_json"]


def test_auto_rejetee_recoit_echeance_purge(cc, tmp_path, config):
    """Une auto_rejetee reçoit purge_prevue_at (= now + purge_rejetees_mois) ; une a_moderer non."""
    _ajout_soumise(cc, tmp_path, _img(100))  # nsfw → auto_rejetee
    _ajout_soumise(cc, tmp_path, _img(300))  # ok → a_moderer
    faux = FauxClassifieur({100: Scores(0.95, 0.1, "x"), 300: Scores(0.05, 0.8, "x")})
    filtrer_lot(cc, config, faux)

    rej = cc.execute(
        "SELECT purge_prevue_at FROM contribution WHERE statut='auto_rejetee'"
    ).fetchone()
    mod = cc.execute(
        "SELECT purge_prevue_at FROM contribution WHERE statut='a_moderer'"
    ).fetchone()
    assert rej["purge_prevue_at"] is not None
    assert mod["purge_prevue_at"] is None


def test_erreur_inference_reste_soumise(cc, tmp_path, config):
    """Une image dont l'inférence lève reste `soumise` (retentée), sans bloquer les autres."""
    id_ko = _ajout_soumise(cc, tmp_path, _img(100))
    id_ok = _ajout_soumise(cc, tmp_path, _img(300))
    faux = FauxClassifieur({300: Scores(0.05, 0.80, "x")}, lever_sur={100})
    res = filtrer_lot(cc, config, faux)

    assert res["erreurs"] == 1 and res["traitees"] == 1
    statuts = {
        r["id"]: r["statut"]
        for r in cc.execute("SELECT id, statut FROM contribution").fetchall()
    }
    assert statuts[id_ko] == "soumise"       # inchangée → repassera au prochain lot
    assert statuts[id_ok] == "a_moderer"


def test_ne_traite_que_les_soumises(cc, tmp_path, config):
    """Le worker ne touche pas les contributions déjà modérées (ex. publiee)."""
    id_pub = _ajout_soumise(cc, tmp_path, _img(300))
    cc.execute("UPDATE contribution SET statut='publiee' WHERE id=?", (id_pub,))
    cc.commit()
    faux = FauxClassifieur({300: Scores(0.05, 0.80, "x")})
    res = filtrer_lot(cc, config, faux)

    assert res["vues"] == 0 and faux.appels == 0
    assert cc.execute("SELECT statut FROM contribution WHERE id=?", (id_pub,)).fetchone()[
        "statut"
    ] == "publiee"


def test_limite_borne_le_lot(cc, tmp_path, config):
    for i in range(3):
        _ajout_soumise(cc, tmp_path, _img(300 + i))  # tailles distinctes → sha distincts
    faux = FauxClassifieur({300: Scores(0.05, 0.8, "x"), 301: Scores(0.05, 0.8, "x"),
                            302: Scores(0.05, 0.8, "x")})
    res = filtrer_lot(cc, config, faux, limite=2)
    assert res["vues"] == 2 and res["traitees"] == 2
    assert cc.execute("SELECT COUNT(*) AS n FROM contribution WHERE statut='soumise'").fetchone()[
        "n"
    ] == 1


# --- dates ----------------------------------------------------------------

def test_plus_mois_simple():
    assert plus_mois("2026-08-07T10:00:00Z", 6) == "2027-02-07T10:00:00Z"


def test_plus_mois_clamp_fin_de_mois():
    # 31 août + 6 mois → février (28 j en 2027, non bissextile) → ramené au 28.
    assert plus_mois("2026-08-31T00:00:00Z", 6) == "2027-02-28T00:00:00Z"
