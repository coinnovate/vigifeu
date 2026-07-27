# Vigifeu — contexte pour Claude Code

Projet : veille des incendies de végétation. Toute la conception est dans docs/
(cadrage, specs 01-04, plan de dev). Lire docs/vigifeu-plan-dev-v0_1.md en premier.

État : Lot 0 terminé. Collecte FIRMS en production sur un VPS (daemon systemd,
15 min). Fixture de référence : tests/fixtures/saumos/ (ne jamais modifier).

Règles :
- tout paramètre dans config/params.toml, jamais de constante magique ;
- observations immuables, jamais de suppression (Spec 01 P1) ;
- ingested_at n'est jamais réécrit (mesure de latence) ;
- lancer pytest après chaque modification ; les 8 tests existants doivent rester verts ;
- commits en français, petits et fréquents.