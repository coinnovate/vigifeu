"""Moteur d'inférence de l'auto-filtre (Spec 10 §5) — NudeNet + CLIP, tout ONNX, CPU.

Adaptateur concret du protocole `filtre.Classifieur`. **Auto-hébergé** : l'image ne quitte
jamais le VPS. Contraintes VPS (§5) : **1 cœur** (`OMP_NUM_THREADS=1`), modèles chargés à la
demande puis relâchés, aucun réseau ni GPU.

⚠️ **Non testé en CI, à vérifier live.** Les modèles ONNX sont déployés hors dépôt (comme les
clés d'API) ; sans eux la factory lève `FiltreIndisponible` et le worker reste inactif — les
contributions demeurent `soumise`, jamais publiées seules (§11). Le câblage tensoriel exact
(préproc NudeNet, sortie du modèle) est à confirmer contre les modèles réels lors de la passe
de vérification, au même titre que les fetchers externes ([[lot1-fetchers-externes-hypotheses]]).

Import **paresseux** de `onnxruntime`/`numpy` : ce module ne s'importe qu'à l'armement du
worker, jamais au chargement de l'API.
"""

from __future__ import annotations

import os
from pathlib import Path

from vigifeu.contrib.filtre import Scores

# Constantes de préprocessing CLIP ViT-B/32 (RGB, normalisation officielle OpenAI).
_CLIP_PX = 224
_CLIP_MEAN = (0.48145466, 0.4578275, 0.40821073)
_CLIP_STD = (0.26862954, 0.26130258, 0.27577711)


class FiltreIndisponible(RuntimeError):
    """Modèles non déployés ou onnxruntime absent → l'auto-filtre ne peut pas tourner (§5)."""


def charger_classifieur(config: dict):
    """Construit le classifieur ONNX depuis la config, ou lève `FiltreIndisponible`.

    Vérifie la présence d'onnxruntime et des trois artefacts (NudeNet, encodeur image CLIP,
    embeddings de labels). Force le mono-cœur avant toute session (contrainte VPS, §5).
    """
    cfg = config["contributions"]
    nudenet = cfg.get("filtre_nudenet_model") or ""
    clip_img = cfg.get("filtre_clip_image_model") or ""
    labels_npz = cfg.get("filtre_clip_labels_npz") or ""
    if not (nudenet and clip_img and labels_npz):
        raise FiltreIndisponible("modèles ONNX non configurés (filtre_* vides)")
    for chemin in (nudenet, clip_img, labels_npz):
        if not Path(chemin).exists():
            raise FiltreIndisponible(f"artefact introuvable : {chemin}")

    os.environ.setdefault("OMP_NUM_THREADS", "1")  # 1 cœur (§5) — avant l'init onnxruntime
    try:
        import onnxruntime as ort  # import paresseux (gros module natif)
    except ImportError as exc:  # pragma: no cover - dépend du déploiement
        raise FiltreIndisponible("onnxruntime absent de l'environnement") from exc

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    fournisseurs = ["CPUExecutionProvider"]
    sess_nsfw = ort.InferenceSession(nudenet, sess_options=opts, providers=fournisseurs)
    sess_clip = ort.InferenceSession(clip_img, sess_options=opts, providers=fournisseurs)
    return FiltreOnnx(sess_nsfw, sess_clip, labels_npz)


class FiltreOnnx:
    """Deux sessions ONNX (NSFW, CLIP image) + embeddings de labels précalculés."""

    def __init__(self, sess_nsfw, sess_clip, labels_npz: str):
        import numpy as np

        self._np = np
        self._nsfw = sess_nsfw
        self._clip = sess_clip
        data = np.load(labels_npz)
        # `feu` / `hors_sujet` : matrices (n_labels, dim) d'embeddings texte L2-normalisés.
        self._emb_feu = data["feu"]
        self._emb_hors = data["hors_sujet"]

    # -- préprocessing ------------------------------------------------------
    def _pil_rgb(self, raw: bytes):
        from io import BytesIO

        from PIL import Image

        return Image.open(BytesIO(raw)).convert("RGB")

    def _preproc_clip(self, img):
        np = self._np
        im = img.resize((_CLIP_PX, _CLIP_PX))
        arr = np.asarray(im, dtype=np.float32) / 255.0
        arr = (arr - np.array(_CLIP_MEAN, np.float32)) / np.array(_CLIP_STD, np.float32)
        return arr.transpose(2, 0, 1)[None, :, :, :]  # NCHW

    # -- inférence ----------------------------------------------------------
    def _score_nsfw(self, img) -> float:
        np = self._np
        im = img.resize((320, 320))  # préproc à confirmer contre le modèle NudeNet déployé
        arr = (np.asarray(im, np.float32) / 255.0).transpose(2, 0, 1)[None]
        out = self._nsfw.run(None, {self._nsfw.get_inputs()[0].name: arr})[0]
        return float(np.ravel(out)[-1])  # dernière sortie = proba nsfw (à confirmer)

    def _score_feu(self, img) -> tuple[float, dict]:
        np = self._np
        arr = self._preproc_clip(img)
        emb = self._clip.run(None, {self._clip.get_inputs()[0].name: arr})[0]
        emb = np.ravel(emb).astype(np.float32)
        emb /= np.linalg.norm(emb) + 1e-8
        sims_feu = self._emb_feu @ emb
        sims_hors = self._emb_hors @ emb
        # zero-shot : softmax sur l'ensemble des labels, masse de proba sur les labels « feu »
        logits = np.concatenate([sims_feu, sims_hors]) * 100.0  # échelle logit CLIP
        ex = np.exp(logits - logits.max())
        probs = ex / ex.sum()
        score_feu = float(probs[: len(sims_feu)].sum())
        detail = {"sim_feu_max": float(sims_feu.max()), "sim_hors_max": float(sims_hors.max())}
        return score_feu, detail

    def classer(self, raw: bytes) -> Scores:
        img = self._pil_rgb(raw)
        score_nsfw = self._score_nsfw(img)
        score_feu, detail = self._score_feu(img)
        return Scores(
            score_nsfw=round(score_nsfw, 4),
            score_feu=round(score_feu, 4),
            moteur="nudenet:onnx;clip:vitb32-onnx",
            detail=detail,
        )
