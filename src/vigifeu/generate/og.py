"""Images Open Graph — génériques par département (Spec 04 §5, décision plan §1.2).

Un lien Sentifeu partagé montre une carte de partage sobre et brandée. En v1, image
**générique par département** (SVG léger, généré une fois) ; le rendu d'une carte
serveur par page est reporté en v1.1 (plan §1.2). Format OG standard 1200×630.
"""

from __future__ import annotations

from pathlib import Path

from vigifeu.generate.writer import write_atomic

# Départements métropole (2A/2B pour la Corse) + DROM.
DEPTS: list[str] = (
    [f"{n:02d}" for n in range(1, 20)] + ["2A", "2B"]
    + [f"{n:02d}" for n in range(21, 96)] + ["971", "972", "973", "974", "976"]
)


def og_svg(marque: str, sous_titre: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">'
        '<rect width="1200" height="630" fill="#f4f4f2"/>'
        '<rect width="1200" height="14" y="616" fill="#c0392b"/>'
        f'<text x="80" y="300" font-family="system-ui,Segoe UI,Roboto,sans-serif" '
        f'font-size="92" font-weight="700" fill="#1a1a1a">{marque}</text>'
        f'<text x="80" y="378" font-family="system-ui,Segoe UI,Roboto,sans-serif" '
        f'font-size="40" fill="#5a5a5a">{sous_titre}</text>'
        '<text x="80" y="560" font-family="system-ui,Segoe UI,Roboto,sans-serif" '
        'font-size="28" fill="#8a8a8a">Veille satellitaire des incendies — NASA FIRMS</text>'
        '</svg>'
    )


def write_og_images(config: dict) -> int:
    """Génère l'image par défaut + une image par département. Retourne le nombre écrit."""
    marque = config["generate"]["marque"]
    out = Path(config["generate"]["site_dir"]) / "static" / "og"
    n = 0
    write_atomic(out / "default.svg", og_svg(marque, "Incendies de végétation en France"))
    n += 1
    for dept in DEPTS:
        write_atomic(out / f"dept-{dept}.svg",
                     og_svg(marque, f"Incendies de végétation — Département {dept}"))
        n += 1
    return n


def og_path(dept: str | None) -> str:
    """Chemin de l'image OG pour un département (ou l'image par défaut)."""
    return f"/static/og/dept-{dept}.svg" if dept else "/static/og/default.svg"
