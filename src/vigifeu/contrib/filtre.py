"""Auto-filtre des contributions (Spec 10 §5) — pré-tri, jamais décision.

Rôle : faire passer chaque `soumise` en `auto_rejetee` (NSFW ou hors-sujet) ou `a_moderer`
(à voir par un humain). **L'auto ne publie jamais seul** (§11).

Ce module ne contient que l'**orchestration** et la **règle de verdict**, toutes deux pures
et testables sans modèle : le moteur d'inférence (NudeNet + CLIP ONNX) est injecté via le
protocole `Classifieur`. L'implémentation réelle vit dans `filtre_onnx.py` (import paresseux,
gros modèles, vérifiée live) ; les tests injectent un faux classifieur.

Résilience (§5) : une image qui échoue/expire **reste `soumise`** (retentée au lot suivant) —
jamais de perte, jamais de blocage du lot.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from vigifeu.contrib.dates import now_iso, plus_mois


@dataclass(frozen=True)
class Scores:
    """Sortie d'inférence pour une image : deux scores + traçabilité (auto_json/moteur_auto)."""

    score_nsfw: float
    score_feu: float
    moteur: str                        # versions modèles, ex. "nudenet:320;clip:vitb32"
    detail: dict = field(default_factory=dict)  # scores par label → auto_json (audit)


class Classifieur(Protocol):
    """Contrat minimal du moteur : octets d'image → `Scores`. Peut lever (géré par le worker)."""

    def classer(self, raw: bytes) -> Scores: ...


def verdict(score_nsfw: float, score_feu: float, *, seuil_nsfw: float, seuil_feu: float) -> str:
    """Règle de tri (§5) : `nsfw` si NSFW ≥ seuil ; sinon `hors_sujet` si feu < seuil ; sinon `ok`."""
    if score_nsfw >= seuil_nsfw:
        return "nsfw"
    if score_feu < seuil_feu:
        return "hors_sujet"
    return "ok"


def statut_pour_verdict(v: str) -> str:
    """`ok` → file humaine (`a_moderer`) ; `nsfw`/`hors_sujet` → `auto_rejetee`."""
    return "a_moderer" if v == "ok" else "auto_rejetee"


def filtrer_lot(
    cc: sqlite3.Connection,
    config: dict,
    classifieur: Classifieur,
    *,
    limite: int = 50,
) -> dict:
    """Traite jusqu'à `limite` contributions `soumise` : inférence → verdict → transition.

    Chaque ligne traitée reçoit `score_nsfw/score_feu`, `auto_verdict`, `auto_json`,
    `moteur_auto` et son nouveau `statut`. Les `auto_rejetee` reçoivent leur échéance de
    purge (`purge_prevue_at`, §9). Une inférence qui lève laisse la ligne **`soumise`**
    (comptée en `erreurs`, retentée plus tard). Retourne un récapitulatif.
    """
    cfg = config["contributions"]
    seuil_nsfw = cfg["seuil_nsfw"]
    seuil_feu = cfg["seuil_feu"]
    purge_mois = cfg["purge_rejetees_mois"]

    rows = cc.execute(
        "SELECT id, image_path FROM contribution WHERE statut='soumise' ORDER BY id LIMIT ?",
        (limite,),
    ).fetchall()

    res = {"vues": len(rows), "traitees": 0, "a_moderer": 0, "auto_rejetee": 0,
           "erreurs": 0, "a_moderer_ids": []}
    for r in rows:
        try:
            raw = Path(r["image_path"]).read_bytes()
            sc = classifieur.classer(raw)
        except Exception:
            res["erreurs"] += 1  # reste `soumise`, retenté au prochain lot (§5)
            continue

        v = verdict(sc.score_nsfw, sc.score_feu, seuil_nsfw=seuil_nsfw, seuil_feu=seuil_feu)
        statut = statut_pour_verdict(v)
        now = now_iso()
        purge_prevue = plus_mois(now, purge_mois) if statut == "auto_rejetee" else None

        # Garde `AND statut='soumise'` : idempotent si un autre worker a déjà pris la ligne.
        cc.execute(
            "UPDATE contribution SET score_nsfw=?, score_feu=?, auto_verdict=?, auto_json=?, "
            "moteur_auto=?, statut=?, purge_prevue_at=? WHERE id=? AND statut='soumise'",
            (sc.score_nsfw, sc.score_feu, v, json.dumps(sc.detail, ensure_ascii=False),
             sc.moteur, statut, purge_prevue, r["id"]),
        )
        res["traitees"] += 1
        res[statut] += 1
        if statut == "a_moderer":
            res["a_moderer_ids"].append(r["id"])  # cibles du mail de modération (§6)

    cc.commit()
    return res
