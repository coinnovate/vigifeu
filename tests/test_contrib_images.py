"""Tests de l'encodage des contributions photo (Spec 10 §4, étape 2).

Deux JPEG bornés, dimensions cohérentes, **sans EXIF**, downscale-only, empreinte sha256
déterministe, et écriture disque de la paire.
"""

from __future__ import annotations

import hashlib
from io import BytesIO

import pytest
from PIL import Image

from vigifeu.contrib.images import ImageInvalide, ecrire_paire, encoder_image


def _png(largeur: int, hauteur: int, couleur=(200, 80, 40), *, exif: bytes | None = None) -> bytes:
    """Fabrique un blob PNG/JPEG de test (couleur unie)."""
    img = Image.new("RGB", (largeur, hauteur), couleur)
    tampon = BytesIO()
    if exif is not None:
        img.save(tampon, format="JPEG", exif=exif)
    else:
        img.save(tampon, format="PNG")
    return tampon.getvalue()


def _dims(jpeg: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(jpeg)) as im:
        return im.width, im.height


def test_deux_jpeg_bornes_et_dimensions():
    """Grande image → affichage borné à max_px, vignette à thumb_px, ratio préservé."""
    enc = encoder_image(_png(4000, 2000), max_px=1600, thumb_px=480, qualite=82)

    # Les deux sorties sont bien des JPEG.
    assert enc.image_jpeg[:3] == b"\xff\xd8\xff"
    assert enc.thumb_jpeg[:3] == b"\xff\xd8\xff"

    # Plus grand côté borné, ratio 2:1 conservé.
    assert (enc.largeur, enc.hauteur) == (1600, 800)
    assert (enc.thumb_largeur, enc.thumb_hauteur) == (480, 240)
    assert _dims(enc.image_jpeg) == (1600, 800)
    assert _dims(enc.thumb_jpeg) == (480, 240)


def test_downscale_only_petite_image_intacte():
    """Une image plus petite que les bornes n'est jamais agrandie."""
    enc = encoder_image(_png(300, 200), max_px=1600, thumb_px=480, qualite=82)
    assert (enc.largeur, enc.hauteur) == (300, 200)          # pas d'upscale vers 1600
    assert (enc.thumb_largeur, enc.thumb_hauteur) == (300, 200)  # < thumb_px → inchangé


def test_sans_exif():
    """Le ré-encodage repart d'un bitmap nu : aucune métadonnée EXIF en sortie (RGPD, §11)."""
    exif = Image.Exif()
    exif[0x010F] = "TestCam"          # Make
    exif[0x0112] = 6                  # Orientation
    blob = _png(800, 600, exif=exif.tobytes())

    enc = encoder_image(blob, max_px=1600, thumb_px=480, qualite=82)
    for jpeg in (enc.image_jpeg, enc.thumb_jpeg):
        with Image.open(BytesIO(jpeg)) as im:
            assert not im.getexif()          # dict EXIF vide
            assert "exif" not in im.info


def test_orientation_exif_appliquee_avant_strip():
    """L'orientation EXIF (6 = rotation 90°) est appliquée puis perdue : image droite, sans tag."""
    exif = Image.Exif()
    exif[0x0112] = 6                          # rotation 90° CW à appliquer
    # Portrait 600x800 marqué "à tourner" → après transpose, paysage 800x600.
    blob = _png(600, 800, exif=exif.tobytes())

    enc = encoder_image(blob, max_px=1600, thumb_px=480, qualite=82)
    assert (enc.largeur, enc.hauteur) == (800, 600)   # dimensions transposées
    with Image.open(BytesIO(enc.image_jpeg)) as im:
        assert not im.getexif()


def test_sha256_est_celui_de_l_image_affichage():
    """L'empreinte porte sur les octets JPEG d'affichage (dédup + traçabilité, §3.4)."""
    enc = encoder_image(_png(1000, 1000), max_px=1600, thumb_px=480, qualite=82)
    assert enc.image_sha256 == hashlib.sha256(enc.image_jpeg).hexdigest()
    assert len(enc.image_sha256) == 64


def test_encodage_deterministe():
    """Même blob + mêmes paramètres → même empreinte (idempotence anti-doublon, §3.4)."""
    blob = _png(1200, 900)
    a = encoder_image(blob, max_px=1600, thumb_px=480, qualite=82)
    b = encoder_image(blob, max_px=1600, thumb_px=480, qualite=82)
    assert a.image_sha256 == b.image_sha256


def test_blob_invalide_leve_image_invalide():
    with pytest.raises(ImageInvalide):
        encoder_image(b"ceci n'est pas une image", max_px=1600, thumb_px=480, qualite=82)


def test_ecrire_paire_hors_public(tmp_path):
    """Écrit affichage + vignette sous une souche, dans un répertoire créé au besoin."""
    enc = encoder_image(_png(2000, 1500), max_px=1600, thumb_px=480, qualite=82)
    cible = tmp_path / "store" / "contrib"          # n'existe pas encore
    image_path, thumb_path = ecrire_paire(enc, cible, enc.image_sha256)

    assert image_path.endswith(f"{enc.image_sha256}.jpg")
    assert thumb_path.endswith(f"{enc.image_sha256}_thumb.jpg")
    from pathlib import Path

    assert Path(image_path).read_bytes() == enc.image_jpeg
    assert Path(thumb_path).read_bytes() == enc.thumb_jpeg
