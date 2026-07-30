"""Bandeau d'indicateurs de la fiche feu — résumé visuel en haut de page.

Chaque tuile = valeur chiffrée (le fait) + mot de niveau + statut de couleur. Le statut
indique un ÉTAT (conditions plus ou moins favorables au feu, danger officiel, intensité
mesurée), jamais une prédiction — cohérent avec « veille, pas alerte ». Couleur JAMAIS
seule (mot de niveau toujours présent). Tuiles conditionnelles : pas de donnée = pas de
tuile. Seuils dans config `[indicateurs]` (jamais en dur).
"""

from __future__ import annotations

from vigifeu.lexique import fr

_STATUTS4 = ("faible", "modere", "eleve", "critique")
_FLECHES = ("↑", "↗", "→", "↘", "↓", "↙", "←", "↖")


def _bande(valeur: float, seuils) -> int:
    """Indice de bande croissante : nombre de seuils atteints (0 = sous tous)."""
    return sum(1 for s in seuils if valeur >= s)


def _fleche_aval(dir_origine_deg: float) -> str:
    """Flèche pointant là où VA le vent (aval = origine + 180°)."""
    aval = (dir_origine_deg + 180) % 360
    return _FLECHES[int((aval + 22.5) % 360 // 45)]


def indicateurs_feu(config: dict, *, wobs, latest, danger_foret, n_communes, actif) -> list[dict]:
    ind = config["indicateurs"]
    t: list[dict] = []

    # 1. Danger Météo des forêts (échelle officielle 1–4).
    if danger_foret is not None:
        try:
            n = int(danger_foret)
        except (TypeError, ValueError):
            n = 0
        if n in (1, 2, 3, 4):
            mots = {1: "Faible", 2: "Modéré", 3: "Élevé", 4: "Très élevé"}
            t.append({"label": "Danger forêt", "valeur": mots[n],
                      "niveau": f"Météo des forêts · niveau {n}/4", "statut": _STATUTS4[n - 1]})

    if wobs is not None:
        # 2. Vent (force + flèche aval + secteur d'origine).
        if wobs["wind_speed_kmh"] is not None:
            b = _bande(wobs["wind_speed_kmh"], ind["vent_seuils"])
            mots = ("Faible", "Modéré", "Fort", "Très fort")
            fleche = _fleche_aval(wobs["wind_dir_deg"]) if wobs["wind_dir_deg"] is not None else ""
            secteur = fr.cardinal_fr(wobs["wind_dir_deg"], 16) if wobs["wind_dir_deg"] is not None else None
            niveau = f"{mots[b]} · secteur {secteur}" if secteur else mots[b]
            t.append({"label": "Vent", "statut": _STATUTS4[b], "niveau": niveau,
                      "valeur": f"{fr.nombre_fr(round(wobs['wind_speed_kmh']))} km/h {fleche}".strip()})
        # 3. Température.
        if wobs["temp_c"] is not None:
            b = _bande(wobs["temp_c"], ind["temp_seuils"])
            mots = ("Douce", "Modérée", "Élevée", "Très élevée")
            t.append({"label": "Température", "statut": _STATUTS4[b], "niveau": mots[b],
                      "valeur": f"{fr.nombre_fr(round(wobs['temp_c']))} °C"})
        # 4. Humidité de l'air (décroissant : sec = danger).
        if wobs["rh_pct"] is not None:
            b = sum(1 for s in ind["humidite_seuils"] if wobs["rh_pct"] < s)
            mots = ("Humide", "Modérée", "Sèche", "Très sec")
            t.append({"label": "Humidité de l'air", "statut": _STATUTS4[b], "niveau": mots[b],
                      "valeur": f"{fr.nombre_fr(round(wobs['rh_pct']))} %"})

    # 5. Puissance thermique (FRP) — jamais « faible » ; feux actifs seulement.
    if actif and latest is not None and latest["frp_total_last_pass_mw"]:
        idx = _bande(latest["frp_total_last_pass_mw"], ind["frp_seuils"])   # 0,1,2
        mots = ("Modéré", "Soutenu", "Intense")
        statuts = ("modere", "eleve", "critique")
        t.append({"label": "Puissance thermique", "statut": statuts[idx], "niveau": mots[idx],
                  "valeur": f"{fr.nombre_fr(round(latest['frp_total_last_pass_mw']))} MW"})

    # 6-7. Ampleur (faits, pas un danger) — statut neutre.
    if latest is not None and latest["area_ha_estimee"]:
        t.append({"label": "Surface estimée", "statut": "neutre", "niveau": "emprise satellite",
                  "valeur": f"{fr.nombre_fr(round(latest['area_ha_estimee']))} ha"})
    if n_communes:
        t.append({"label": "Communes concernées", "statut": "neutre", "niveau": "emprise + proximité",
                  "valeur": fr.nombre_fr(n_communes)})
    return t
