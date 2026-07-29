"""Écriture atomique des pages (Spec 04 P5).

Une page est écrite dans un fichier temporaire du même répertoire puis renommée
(`os.replace`, atomique sur le même système de fichiers) : le site n'expose jamais
une page à moitié générée, même si le générateur est interrompu en plein rendu.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def page_path(site_dir: str | Path, page_type: str, page_ref: str) -> Path:
    """Chemin de sortie d'une page (arborescence Spec 04 §4).

    * carte    → index.html à la racine
    * feu      → /feux/{public_id}/index.html
    * commune  → /communes/{slug_ou_ref}/index.html
    """
    root = Path(site_dir)
    if page_type == "carte":
        return root / "index.html"
    if page_type == "feu":
        return root / "feux" / page_ref / "index.html"
    if page_type == "commune":
        return root / "communes" / page_ref / "index.html"
    raise ValueError(f"type de page inconnu : {page_type}")


def write_atomic(path: str | Path, content: str) -> Path:
    """Écrit `content` en UTF-8 à `path` de façon atomique. Retourne le chemin final."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        # mkstemp force 0600 (sécurité) et os.replace conserve ce mode : sans correction,
        # tout fichier généré est illisible par Nginx (www-data) → 403 à chaque régén.
        # On applique un mode public dérivé de l'umask du process (0644 avec umask 022).
        cur = os.umask(0)
        os.umask(cur)
        os.chmod(tmp, 0o666 & ~cur)
        os.replace(tmp, path)                     # atomique (même répertoire)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
