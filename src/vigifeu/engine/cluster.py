"""Clustering spatio-temporel incrémental par passage (Spec 02 §4).

Leçon du prototype : le clustering purement spatial sur données cumulées fusionne
des événements distincts. On rattache donc **incrémentalement, hotspot par
hotspot dans l'ordre des passages**, chaque nouveau pixel cherchant un FireEvent
récent et proche.

Décisions (§4.1) : 0 candidat → création ; 1 → rattachement ; ≥2 → fusion (le plus
ancien absorbe, les autres passent `fusionne` avec `merged_into` + `fe_fe_rel`).
Reprises (§4.3) : rattacher à un feu non actif le rouvre.

--- Note d'interprétation des seuils temporels ---
La spec superpose T_silence (24 h, §4.5), T_gap (48 h, §4.2) et T_reprise (7 j,
§4.3). La **fenêtre de candidature au rattachement est T_gap** : c'est ce que
tranchent à la fois le jalon (§10.1, « l'événement du 20/07 à 12,6 km reste
distinct » = 2 jours de silence ⇒ nouvel événement) et §4.3 lui-même (« encore
candidat au rattachement, T_gap non écoulé »). Pilotée par l'écart `g` entre le
hotspot et la dernière détection du feu candidat :

  g < T_silence (24 h)      → rattachement continu (feu actif) ;
  T_silence ≤ g < T_gap      → REPRISE : rattachement à un feu plus_detecte
                              (retour actif, `reprise=true`) ;
  g ≥ T_gap (48 h)           → hors candidature : nouvel événement ; s'il existe un
                              feu passé à moins de D_link, relation `proche_de`.

T_reprise (7 j) borne le passage plus_detecte → archive (`apply_lifecycle`) : au
sens de la spec, un feu reste « reprenable » tant qu'il n'est pas archivé, mais la
candidature effective au *rattachement spatial* reste bornée par T_gap — sans quoi
un feu voisin qui s'étend rouvrirait un foyer éteint distinct (le bug du prototype).

La distance de rattachement `D_link` passe à `d_link_grands_feux` au-delà de
`n_hs_grand` hotspots (sautes de feu). Les étiquettes de cycle de vie
(actif/plus_detecte/archive) sont posées par `apply_lifecycle` contre l'horloge
des données — elles pilotent la météo et l'affichage, pas la décision de
rattachement (robuste au rejeu, où les étiquettes peuvent être en retard).

Le versionnage, la qualification et les mesures sont d'autres étapes du cycle
(§3) : ce module ne touche que `fire_event` (identité, cycle de vie, first/last
acq), `hotspot_raw.fire_event_id` (membership courante) et `fe_fe_rel`. Seule
exception : `apply_lifecycle` émet `regen_queue` pour un feu publié qui change
d'étiquette (§4.5, « dernière régénération ») — sinon la fiche resterait figée
sur « actif » puisque, sans nouveau hotspot, plus rien ne la ré-enfile.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime, timedelta

from vigifeu.engine import geo
from vigifeu.engine.regen import enqueue, enqueue_fire_update

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _parse(iso: str) -> datetime:
    return datetime.strptime(iso, _ISO)


def _fmt(dt: datetime) -> str:
    return dt.strftime(_ISO)


def _now_iso() -> str:
    return datetime.now(UTC).strftime(_ISO)


class _Ev:
    """État en mémoire d'un FireEvent pendant un cycle (miroir de la base)."""

    __slots__ = ("id", "first", "last", "lifecycle", "n", "xs", "ys",
                 "minx", "maxx", "miny", "maxy")

    def __init__(self, id, first, last, lifecycle):
        self.id = id
        self.first = first
        self.last = last
        self.lifecycle = lifecycle
        self.n = 0
        self.xs: list[float] = []
        self.ys: list[float] = []
        self.minx = self.miny = math.inf
        self.maxx = self.maxy = -math.inf

    def add_point(self, x: float, y: float, t: datetime) -> None:
        self.xs.append(x)
        self.ys.append(y)
        self.n += 1
        self.minx, self.maxx = min(self.minx, x), max(self.maxx, x)
        self.miny, self.maxy = min(self.miny, y), max(self.maxy, y)
        self.last = max(self.last, t)
        self.first = min(self.first, t)

    def min_dist(self, x: float, y: float) -> float:
        best = math.inf
        for mx, my in zip(self.xs, self.ys):
            d = math.hypot(x - mx, y - my)
            if d < best:
                best = d
        return best


def _load_events(conn: sqlite3.Connection) -> dict[int, _Ev]:
    """Charge en mémoire les feux non fusionnés et leurs hotspots (projetés L93)."""
    events: dict[int, _Ev] = {}
    for r in conn.execute(
        "SELECT id, first_acq_at, last_acq_at, lifecycle FROM fire_event "
        "WHERE lifecycle != 'fusionne'"
    ):
        events[r["id"]] = _Ev(
            r["id"],
            _parse(r["first_acq_at"]) if r["first_acq_at"] else datetime.max,
            _parse(r["last_acq_at"]) if r["last_acq_at"] else datetime.min,
            r["lifecycle"],
        )
    members = conn.execute(
        "SELECT id, lat, lon, acq_at, fire_event_id FROM hotspot_raw "
        "WHERE fire_event_id IS NOT NULL"
    ).fetchall()
    proj = geo.project_rows([(m["id"], m["lat"], m["lon"]) for m in members])
    for m in members:
        ev = events.get(m["fire_event_id"])
        if ev is not None:
            x, y = proj[m["id"]]
            ev.add_point(x, y, _parse(m["acq_at"]))
    return events


def cluster_new_hotspots(conn: sqlite3.Connection, config: dict, *, stamp: str | None = None) -> dict:
    """Rattache les hotspots libres (ni feu, ni source fixe) aux FireEvents (§4.1).

    Traite `fire_event_id IS NULL AND fixed_source_id IS NULL AND overpass_id IS
    NOT NULL`, dans l'ordre des passages. Idempotent : sans hotspot libre, no-op.
    Retourne {created, attached, merged, reprises:set, touched:set}.
    """
    cl = config["clustering"]
    d_link = cl["d_link_m"]
    d_link_grand = cl["d_link_grands_feux_m"]
    n_hs_grand = cl["n_hs_grand"]
    t_silence = timedelta(hours=cl["t_silence_h"])
    t_gap = timedelta(hours=cl["t_gap_h"])
    stamp = stamp or _now_iso()

    pending = conn.execute(
        "SELECT id, lat, lon, acq_at FROM hotspot_raw "
        "WHERE fire_event_id IS NULL AND fixed_source_id IS NULL "
        "AND overpass_id IS NOT NULL ORDER BY acq_at, id"
    ).fetchall()
    if not pending:
        return {"created": 0, "attached": 0, "merged": 0, "reprises": set(), "touched": set()}

    events = _load_events(conn)
    proj = geo.project_rows([(h["id"], h["lat"], h["lon"]) for h in pending])

    created = attached = merged = 0
    reprises: set[int] = set()
    touched: set[int] = set()

    def dlink_of(ev: _Ev) -> float:
        return d_link_grand if ev.n > n_hs_grand else d_link

    for hs in pending:
        hid = hs["id"]
        x, y = proj[hid]
        t = _parse(hs["acq_at"])

        candidates: list[_Ev] = []      # rattachables : proche ET récent (g < T_reprise)
        near_old: _Ev | None = None     # proche mais trop ancien → proche_de
        near_old_d = math.inf

        for ev in events.values():
            if ev.lifecycle == "fusionne":
                continue
            dl = dlink_of(ev)
            # Pré-filtre bbox (bon marché) avant la distance aux membres.
            if x < ev.minx - dl or x > ev.maxx + dl or y < ev.miny - dl or y > ev.maxy + dl:
                continue
            d = ev.min_dist(x, y)
            if d >= dl:
                continue
            gap = t - ev.last
            if ev.lifecycle in ("actif", "plus_detecte") and gap < t_gap:
                candidates.append(ev)
            elif gap >= t_gap and d < near_old_d:
                near_old, near_old_d = ev, d  # proche spatialement mais éteint → proche_de

        if not candidates:
            eid = _create_event(conn, stamp, hs["acq_at"])
            ev = _Ev(eid, t, t, "actif")
            ev.add_point(x, y, t)
            events[eid] = ev
            conn.execute("UPDATE hotspot_raw SET fire_event_id=? WHERE id=?", (eid, hid))
            created += 1
            touched.add(eid)
            if near_old is not None:
                _add_rel(conn, eid, near_old.id, "proche_de", stamp,
                         note=f"nouvel événement à {round(near_old_d)} m d'un feu passé")
            continue

        if len(candidates) > 1:
            target = _merge(conn, candidates, stamp)
            merged += len(candidates) - 1
        else:
            target = candidates[0]

        gap = t - target.last
        # Reprise : rattachement à un feu qui s'était tu (§4.3). Fondé sur l'écart
        # réel (robuste au rejeu où l'étiquette de cycle de vie peut être en retard).
        if gap >= t_silence:
            if target.lifecycle != "actif":
                conn.execute(
                    "UPDATE fire_event SET lifecycle='actif' WHERE id=?", (target.id,)
                )
                target.lifecycle = "actif"
            reprises.add(target.id)

        target.add_point(x, y, t)
        conn.execute("UPDATE hotspot_raw SET fire_event_id=? WHERE id=?", (target.id, hid))
        conn.execute(
            "UPDATE fire_event SET first_acq_at=?, last_acq_at=? WHERE id=?",
            (_fmt(target.first), _fmt(target.last), target.id),
        )
        attached += 1
        touched.add(target.id)

    conn.commit()
    return {"created": created, "attached": attached, "merged": merged,
            "reprises": reprises, "touched": touched}


def _create_event(conn: sqlite3.Connection, stamp: str, acq_at: str) -> int:
    return conn.execute(
        "INSERT INTO fire_event (created_at, first_acq_at, last_acq_at, lifecycle) "
        "VALUES (?, ?, ?, 'actif')",
        (stamp, acq_at, acq_at),
    ).lastrowid


def _merge(conn: sqlite3.Connection, candidates: list[_Ev], stamp: str) -> _Ev:
    """Fusionne plusieurs candidats : le plus ancien absorbe (§4.1, §4.4)."""
    absorber = min(candidates, key=lambda e: (e.first, e.id))
    for ev in candidates:
        if ev.id == absorber.id:
            continue
        conn.execute(
            "UPDATE fire_event SET lifecycle='fusionne', merged_into=? WHERE id=?",
            (absorber.id, ev.id),
        )
        conn.execute(
            "UPDATE hotspot_raw SET fire_event_id=? WHERE fire_event_id=?",
            (absorber.id, ev.id),
        )
        _add_rel(conn, ev.id, absorber.id, "fusionne_dans", stamp,
                 note="deux départs distincts se sont rejoints")
        # Absorption en mémoire.
        absorber.xs.extend(ev.xs)
        absorber.ys.extend(ev.ys)
        absorber.n += ev.n
        absorber.minx = min(absorber.minx, ev.minx)
        absorber.maxx = max(absorber.maxx, ev.maxx)
        absorber.miny = min(absorber.miny, ev.miny)
        absorber.maxy = max(absorber.maxy, ev.maxy)
        absorber.first = min(absorber.first, ev.first)
        absorber.last = max(absorber.last, ev.last)
        ev.lifecycle = "fusionne"
    # first_acq recalculé au moment de la fusion (donnée contractuelle §4.4).
    conn.execute(
        "UPDATE fire_event SET first_acq_at=?, last_acq_at=? WHERE id=?",
        (_fmt(absorber.first), _fmt(absorber.last), absorber.id),
    )
    return absorber


def _add_rel(conn: sqlite3.Connection, fe_id: int, related_id: int,
             rel_type: str, stamp: str, note: str | None = None) -> None:
    conn.execute(
        "INSERT INTO fe_fe_rel (fire_event_id, related_fire_event_id, rel_type, created_at, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (fe_id, related_id, rel_type, stamp, note),
    )


def apply_lifecycle(conn: sqlite3.Connection, config: dict, *, clock: str | None = None,
                    stamp: str | None = None) -> dict:
    """Transitions de cycle de vie (§4.5) contre l'horloge des données.

    actif → plus_detecte après `t_silence_h` ; plus_detecte → archive après
    `t_reprise_days`. `clock` par défaut = dernière acquisition connue (rejeu) ;
    en production, passer l'heure courante. Retourne les compteurs de transitions.

    Un feu publié qui change d'étiquette est ré-enfilé dans `regen_queue` (§4.5) :
    - → plus_detecte : seul le libellé de la fiche change (« plus détecté
      depuis… ») — la carte et la situation en cours des communes gardent le feu ;
    - → archive : le feu quitte la carte et la situation en cours ; on régénère
      donc fiche + carte + communes à relation ouverte.
    Sans nouveau hotspot, rien d'autre ne ré-enfilerait ces pages : elles
    resteraient figées sur l'état antérieur.
    """
    cl = config["clustering"]
    t_silence = timedelta(hours=cl["t_silence_h"])
    t_reprise = timedelta(days=cl["t_reprise_days"])

    if clock is None:
        row = conn.execute("SELECT MAX(last_acq_at) AS m FROM fire_event").fetchone()
        if not row or not row["m"]:
            return {"to_plus_detecte": 0, "to_archive": 0}
        clock = row["m"]
    now = _parse(clock)
    if stamp is None:
        stamp = clock

    to_pd = to_arch = 0
    for r in conn.execute(
        "SELECT id, last_acq_at, lifecycle, public_id FROM fire_event "
        "WHERE lifecycle IN ('actif', 'plus_detecte')"
    ).fetchall():
        if not r["last_acq_at"]:
            continue
        silence = now - _parse(r["last_acq_at"])
        if silence >= t_reprise:
            conn.execute("UPDATE fire_event SET lifecycle='archive' WHERE id=?", (r["id"],))
            to_arch += 1
            if r["public_id"]:
                communes = [c["code_insee"] for c in conn.execute(
                    "SELECT DISTINCT code_insee FROM fe_commune_rel "
                    "WHERE fire_event_id=? AND valid_to IS NULL", (r["id"],)
                ).fetchall()]
                enqueue_fire_update(conn, r["id"], communes, stamp=stamp, trigger="lifecycle")
        elif silence >= t_silence and r["lifecycle"] == "actif":
            conn.execute("UPDATE fire_event SET lifecycle='plus_detecte' WHERE id=?", (r["id"],))
            to_pd += 1
            if r["public_id"]:
                enqueue(conn, "feu", str(r["id"]), stamp=stamp, trigger="lifecycle")
    conn.commit()
    return {"to_plus_detecte": to_pd, "to_archive": to_arch}
