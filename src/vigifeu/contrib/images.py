"""Encodage des contributions photo (Spec 10 §4, étape 2).

Une contribution arrive en **blob** (canvas `getUserMedia`, §4). Avant tout stockage on
la **ré-encode** en deux JPEG bornés :

- **image d'affichage** — plus grand côté ramené à `max_px` (lightbox) ;
- **vignette** — plus grand côté ramené à `thumb_px` (grille du widget, §7).

Trois garanties portées ici :

1. **Downscale-only** — jamais d'agrandissement (`thumbnail` ne fait que réduire) : on ne
   fabrique pas de faux pixels, et une petite image reste à sa taille.
2. **Sans EXIF** — le ré-encodage repart d'un bitmap nu ; aucune métadonnée n'est réécrite
   (RGPD, §11 : ni géoloc EXIF, ni marque d'appareil). L'orientation EXIF éventuelle est
   d'abord **appliquée** (`exif_transpose`) pour ne pas afficher de photo couchée, puis
   perdue avec le reste.
3. **`sha256` de l'image d'affichage** — empreinte de dédup + traçabilité qui **survit à la
   purge** (§3.4). Calculée sur les octets JPEG finaux (déterministes).

L'écriture disque (`ecrire_paire`) vise un répertoire **hors racine publique** : les images
ne sont jamais servies en statique, seulement via l'API après contrôle (§7.2).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


class ImageInvalide(ValueError):
    """Le blob reçu n'est pas une image décodable (rejet propre côté endpoint, §4)."""


@dataclass(frozen=True)
class ImageEncodee:
    """Résultat d'encodage : deux JPEG en mémoire + empreinte + dimensions (§3.1)."""

    image_jpeg: bytes
    thumb_jpeg: bytes
    image_sha256: str
    largeur: int
    hauteur: int
    thumb_largeur: int
    thumb_hauteur: int


def _borner(img: Image.Image, cote_max: int) -> Image.Image:
    """Réduit une copie pour que le plus grand côté ≤ `cote_max` (jamais d'agrandissement)."""
    copie = img.copy()
    copie.thumbnail((cote_max, cote_max), Image.LANCZOS)  # in-place, downscale-only
    return copie


def _en_jpeg(img: Image.Image, qualite: int) -> bytes:
    """Encode en JPEG (sans EXIF ni ICC : bitmap nu → aucune métadonnée réécrite)."""
    tampon = BytesIO()
    img.save(tampon, format="JPEG", quality=qualite, optimize=True)
    return tampon.getvalue()


def encoder_image(raw: bytes, *, max_px: int, thumb_px: int, qualite: int) -> ImageEncodee:
    """Ré-encode un blob en (affichage `max_px`, vignette `thumb_px`) JPEG, sans EXIF.

    Lève `ImageInvalide` si `raw` n'est pas une image décodable.
    """
    try:
        source = Image.open(BytesIO(raw))
        source.load()  # force le décodage ici (erreur propre, pas plus tard au save)
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageInvalide("blob non décodable en image") from exc

    # Honore l'orientation EXIF éventuelle AVANT de la perdre (photo droite), puis aplatit en RGB
    # (JPEG n'a ni alpha ni palette). `exif_transpose` retourne une image sans le tag d'orientation.
    source = ImageOps.exif_transpose(source).convert("RGB")

    affichage = _borner(source, max_px)
    vignette = _borner(affichage, thumb_px)  # dérive de l'affichage : cohérence + moins de rééchantillonnage

    image_jpeg = _en_jpeg(affichage, qualite)
    thumb_jpeg = _en_jpeg(vignette, qualite)

    return ImageEncodee(
        image_jpeg=image_jpeg,
        thumb_jpeg=thumb_jpeg,
        image_sha256=hashlib.sha256(image_jpeg).hexdigest(),
        largeur=affichage.width,
        hauteur=affichage.height,
        thumb_largeur=vignette.width,
        thumb_hauteur=vignette.height,
    )


def ecrire_paire(enc: ImageEncodee, repertoire: str | Path, souche: str) -> tuple[str, str]:
    """Écrit `{souche}.jpg` (affichage) et `{souche}_thumb.jpg` (vignette) dans `repertoire`.

    `repertoire` doit être **hors racine publique** (§7.2) : les images ne sont jamais servies
    en statique. Le répertoire est créé au besoin. Retourne `(image_path, thumb_path)`.
    """
    base = Path(repertoire)
    base.mkdir(parents=True, exist_ok=True)
    image_path = base / f"{souche}.jpg"
    thumb_path = base / f"{souche}_thumb.jpg"
    image_path.write_bytes(enc.image_jpeg)
    thumb_path.write_bytes(enc.thumb_jpeg)
    return str(image_path), str(thumb_path)
