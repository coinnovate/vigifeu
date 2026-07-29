"""Écriture atomique (Spec 04 P5) : le fichier publié doit être LISIBLE par Nginx.

Régression : `tempfile.mkstemp` force le mode 0600 et `os.replace` le conserve → les
pages générées étaient illisibles par www-data (403 à chaque régénération). `write_atomic`
applique désormais un mode public dérivé de l'umask.
"""

from __future__ import annotations

import os
import stat

from vigifeu.generate.writer import write_atomic


def test_write_atomic_contenu(tmp_path):
    p = write_atomic(tmp_path / "sub" / "x.html", "<html>ok</html>")
    assert p.read_text(encoding="utf-8") == "<html>ok</html>"


def test_write_atomic_fichier_lisible(tmp_path):
    """Sous umask 022, le fichier publié est 0644 (lisible par tous) — pas 0600."""
    if os.name != "posix":
        return  # perms POSIX seulement ; le contenu est couvert ci-dessus
    old = os.umask(0o022)
    try:
        p = write_atomic(tmp_path / "y.html", "x")
        mode = stat.S_IMODE(os.stat(p).st_mode)
        assert mode == 0o644, f"mode inattendu {oct(mode)} (Nginx renverrait 403)"
    finally:
        os.umask(old)
