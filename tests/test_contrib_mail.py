"""Tests de la construction des mails (Spec 10 §6, étape 6c).

Mails de modération (vignette inline + détails + liens d'action signés en GET) et de
notification de publication ; sélection SMTP depuis l'environnement. L'envoi réseau n'est
pas testé (adaptateur mince) ; le message et sa structure le sont.
"""

from __future__ import annotations

from email.message import EmailMessage

from vigifeu.contrib.mail import (
    Mail,
    MailerSMTP,
    mail_moderation,
    mail_publication,
    mailer_depuis_env,
)

BASE = "https://sentifeu.fr"


def test_mail_moderation_contient_liens_details_et_vignette():
    tokens = {"publier": "tokP", "rejeter": "tokR", "blacklister": "tokB"}
    m = mail_moderation(
        destinataire="mod@sentifeu.fr",
        base_url=BASE,
        tokens=tokens,
        vignette=b"\xff\xd8\xffvignette",
        feu_public_id="2026-saumos",
        captured_at="2026-08-07T10:00:00Z",
        distance_km=4.4,
        score_nsfw=0.02,
        score_feu=0.88,
    )
    assert m.destinataire == "mod@sentifeu.fr"
    # Un lien d'action par action, vers /api/contrib/action/{token}.
    for tok in ("tokP", "tokR", "tokB"):
        assert f"{BASE}/api/contrib/action/{tok}" in m.html
        assert f"{BASE}/api/contrib/action/{tok}" in m.texte
    # Détails présents + vignette inline attachée sous un cid.
    assert "2026-saumos" in m.html and "4.4 km" in m.html
    assert m.images_inline and next(iter(m.images_inline.values())) == b"\xff\xd8\xffvignette"
    assert f'cid:{next(iter(m.images_inline))}' in m.html


def test_mail_moderation_feu_non_rattache():
    m = mail_moderation(
        destinataire="mod@sentifeu.fr", base_url=BASE,
        tokens={"publier": "a", "rejeter": "b", "blacklister": "c"},
        vignette=b"x", feu_public_id=None, captured_at="2026-08-07T10:00:00Z",
        distance_km=None, score_nsfw=None, score_feu=None,
    )
    assert "non rattaché" in m.html


def test_mail_publication_avec_et_sans_feu():
    avec = mail_publication(destinataire="a@b.fr", base_url=BASE, feu_public_id="2026-saumos")
    assert "publiée" in avec.html and f"{BASE}/feux/2026-saumos" in avec.html
    sans = mail_publication(destinataire="a@b.fr", base_url=BASE, feu_public_id=None)
    assert "publiée" in sans.html and "/feux/" not in sans.html


def test_mailer_smtp_construit_un_message_multipart_avec_image():
    """Vérifie la structure du message construit (sans réseau) via un SMTP simulé."""
    envoyes = []

    class FauxSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, **k):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            envoyes.append(msg)

    import vigifeu.contrib.mail as mailmod

    orig = mailmod.smtplib.SMTP
    mailmod.smtplib.SMTP = FauxSMTP
    try:
        mailer = MailerSMTP(host="h", port=587, user="u", password="p",
                            expediteur="Sentifeu <no-reply@sentifeu.fr>")
        mailer.envoyer(Mail("mod@sentifeu.fr", "sujet", "<p>hi</p>", "hi",
                            {"vignette": b"\xff\xd8\xffimg"}))
    finally:
        mailmod.smtplib.SMTP = orig

    assert len(envoyes) == 1
    msg = envoyes[0]
    assert isinstance(msg, EmailMessage)
    assert msg["To"] == "mod@sentifeu.fr" and msg["Subject"] == "sujet"
    assert msg["From"] == "Sentifeu <no-reply@sentifeu.fr>"
    # Une image jpeg embarquée quelque part dans le message.
    types = {p.get_content_type() for p in msg.walk()}
    assert "image/jpeg" in types


def test_mailer_smtp_port_465_utilise_ssl(monkeypatch):
    """Port 465 → SMTP_SSL (SSL implicite), pas STARTTLS (cas o2switch/OVH)."""
    utilises = {"ssl": False, "plain": False}

    class FauxSMTP:
        def __init__(self, *a, **k):
            utilises["plain"] = True

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, **k):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            pass

    class FauxSMTPSSL(FauxSMTP):
        def __init__(self, *a, **k):
            utilises["ssl"] = True

    import vigifeu.contrib.mail as mailmod

    monkeypatch.setattr(mailmod.smtplib, "SMTP", FauxSMTP)
    monkeypatch.setattr(mailmod.smtplib, "SMTP_SSL", FauxSMTPSSL)
    MailerSMTP(host="h", port=465, user="u", password="p", expediteur="x").envoyer(
        Mail("a@b.fr", "s", "<p>h</p>", "h")
    )
    assert utilises["ssl"] and not utilises["plain"]


def test_mailer_depuis_env_absent_retourne_none(monkeypatch):
    monkeypatch.delenv("CONTRIB_SMTP_HOST", raising=False)
    config = {"contributions": {"mail_expediteur": "x"}}
    assert mailer_depuis_env(config) is None


def test_mailer_depuis_env_present_construit(monkeypatch):
    monkeypatch.setenv("CONTRIB_SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("CONTRIB_SMTP_PORT", "2525")
    config = {"contributions": {"mail_expediteur": "Sentifeu <no-reply@sentifeu.fr>"}}
    mailer = mailer_depuis_env(config)
    assert isinstance(mailer, MailerSMTP)
    assert mailer._host == "smtp.example.org" and mailer._port == 2525


def test_mailer_depuis_env_tolere_commentaire_inline(monkeypatch):
    """Commentaire inline (piège .env systemd) : récupéré, host/port nettoyés."""
    monkeypatch.setenv("CONTRIB_SMTP_HOST", "goeland.o2switch.net   # ou mail.sentifeu.fr")
    monkeypatch.setenv("CONTRIB_SMTP_PORT", "587   # STARTTLS")
    config = {"contributions": {"mail_expediteur": "x"}}
    mailer = mailer_depuis_env(config)
    assert isinstance(mailer, MailerSMTP)
    assert mailer._host == "goeland.o2switch.net" and mailer._port == 587


def test_mailer_depuis_env_port_invalide_desactive_sans_crash(monkeypatch):
    """Un port réellement illisible désactive l'e-mail (None), sans faire planter le service."""
    monkeypatch.setenv("CONTRIB_SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("CONTRIB_SMTP_PORT", "pas-un-port")
    assert mailer_depuis_env({"contributions": {"mail_expediteur": "x"}}) is None
