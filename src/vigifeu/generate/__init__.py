"""Générateur statique Vigifeu (Spec 04).

`page = gabarit(données, lexique)` — fonction pure des données (P1) : deux exécutions
sur les mêmes données produisent le même HTML, ce qui rend la régénération sélective
sûre et le golden file (§9.2) possible. Le générateur consomme `regen_queue` (P2),
n'écrit jamais « tout le site », et publie chaque page par renommage atomique (P5).
"""
