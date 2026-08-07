"""Tests des jobs planifiés du service contributif (Spec 10 §5/§9).

Auto-filtre (traite + notifie les a_moderer), dégradation si ONNX absent, envoi du mail de
modération, purge, et composition du planificateur.
"""

from __future__ import annotations

import copy
import hashlib
from io import BytesIO

import pytest
from PIL import Image

import vigifeu.contrib.jobs as jobs
from vigifeu.contrib.db import connect_contrib, migrate_contrib
from vigifeu.contrib.filtre import Scores
from vigifeu.contrib.filtre_onnx import FiltreIndisponible
from vigifeu.model.db import load_config

T0 = "2026-08-07T10:00:00Z"


@pytest.fixture()
def config(tmp_path):
    c = copy.deepcopy(load_config("config/params.toml"))
    c["contributions"]["db_path"] = str(tmp_path / "contributions.db")
    c["general"]["db_path"] = str(tmp_path / "absente.db")  # socle absente → feu_public_id None
    return c


def _img(tmp_path, sha):
    p = tmp_path / f"{sha}.jpg"
    Image.new("RGB", (400, 300), (200, 80, 40)).save(p, format="JPEG")
    return str(p)


def _ajout_soumise(cc, tmp_path, sha):
    chemin = _img(tmp_path, sha)
    cc.execute(
        "INSERT INTO contribution (captured_at, image_path, thumb_path, image_sha256, "
        "consentement_at, cgu_version, statut, created_at) VALUES (?,?,?,?,?,?, 'soumise', ?)",
        (T0, chemin, chemin, sha, T0, "2026-08", T0),
    )
    cc.commit()


class FauxClassifieur:
    def classer(self, raw):
        return Scores(0.05, 0.90, "faux")  # ok → a_moderer


class FauxMailer:
    def __init__(self):
        self.envoyes = []

    def envoyer(self, mail):
        self.envoyes.append(mail)


def test_job_filtre_traite_et_notifie(config, tmp_path, monkeypatch):
    cc = connect_contrib(config["contributions"]["db_path"])
    migrate_contrib(cc)
    _ajout_soumise(cc, tmp_path, "a")
    _ajout_soumise(cc, tmp_path, "b")
    cc.close()

    mailer = FauxMailer()
    monkeypatch.setattr(jobs, "charger_classifieur", lambda cfg: FauxClassifieur())
    monkeypatch.setattr(jobs, "mailer_depuis_env", lambda cfg: mailer)
    monkeypatch.setenv("CONTRIB_MODERATION_EMAIL", "mod@sentifeu.fr")
    monkeypatch.setenv("CONTRIB_HASH_SECRET", "secret")

    res = jobs.job_filtre(config)
    assert res["a_moderer"] == 2 and res["traitees"] == 2
    # Un mail de modération par contribution passée en a_moderer.
    assert len(mailer.envoyes) == 2
    assert all("/api/contrib/action/" in m.html for m in mailer.envoyes)


def test_job_filtre_degrade_si_onnx_absent(config, tmp_path, monkeypatch):
    cc = connect_contrib(config["contributions"]["db_path"])
    migrate_contrib(cc)
    _ajout_soumise(cc, tmp_path, "a")
    cc.close()

    def _indispo(cfg):
        raise FiltreIndisponible("modèles absents")

    monkeypatch.setattr(jobs, "charger_classifieur", _indispo)
    res = jobs.job_filtre(config)
    assert res == {"indisponible": True}
    # La contribution reste soumise (jamais publiée seule).
    cc = connect_contrib(config["contributions"]["db_path"])
    statut = cc.execute("SELECT statut FROM contribution").fetchone()["statut"]
    cc.close()
    assert statut == "soumise"


def test_envoyer_mail_moderation_sans_config_noop(config, tmp_path):
    cc = connect_contrib(config["contributions"]["db_path"])
    migrate_contrib(cc)
    cc.execute("INSERT INTO contribution (captured_at, image_sha256, consentement_at, "
               "cgu_version, statut, created_at) VALUES (?,?,?,?, 'a_moderer', ?)",
               (T0, "s", T0, "2026-08", T0))
    cc.commit()
    # Sans mailer/destinataire/secret → aucun envoi.
    assert jobs.envoyer_mail_moderation(config, cc, 1, mailer=None, dest=None, secret=None) is False
    cc.close()


def test_job_purge_smoke(config, tmp_path):
    cc = connect_contrib(config["contributions"]["db_path"])
    migrate_contrib(cc)
    cc.execute("INSERT INTO contribution (captured_at, image_sha256, consentement_at, "
               "cgu_version, statut, purge_prevue_at, created_at) "
               "VALUES (?,?,?,?, 'rejetee', '2026-01-01T00:00:00Z', ?)",
               (T0, "s", T0, "2026-08", T0))
    cc.commit()
    cc.close()
    res = jobs.job_purge(config)
    assert res["rejetees_purgees"] == 1


def test_construire_planificateur_a_deux_jobs(config):
    sched = jobs.construire_planificateur(config)
    ids = {j.id for j in sched.get_jobs()}
    assert ids == {"contrib_filtre", "contrib_purge"}
