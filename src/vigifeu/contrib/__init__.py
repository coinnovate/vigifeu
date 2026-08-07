"""Canal contributif photo public (Spec 10).

Le dépôt (capture in-app) et l'affichage (widget) vivent sur sentifeu.fr ; le dynamique
(upload, service d'images, auto-filtre, modération, purge) est porté par une mini-API
same-origin (Flask, service systemd distinct) sous `sentifeu.fr/api/contrib/...`.

Cloisonnement structurant (Spec 10 §2) : les contributions vivent dans leur PROPRE base
(l'API en est l'écrivain) et la socle n'est lue qu'en LECTURE SEULE — ce qui préserve
l'invariant « un seul écrivain sur la socle = le daemon » (plan §1.1).
"""
