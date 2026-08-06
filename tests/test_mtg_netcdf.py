"""Tests du parsing netCDF ListProduct (Spec 07 §2, étape 3).

On fabrique une fixture netCDF synthétique avec `netCDF4` (structure ListProduct : latitude,
longitude, fire_radiative_power, confidence, time CF) — aucun fichier 0682 réel requis. On couvre :
extraction des champs, décodage du temps CF, filtrage par bbox France, lecture depuis des OCTETS
(cas du fetcher) et depuis un chemin, la tolérance aux noms de variables, et l'erreur si lat/lon manquent.
"""

from __future__ import annotations

import datetime as dt

import netCDF4
import numpy as np
import pytest

from vigifeu.ingest import mtg_netcdf
from vigifeu.model.db import load_config

# Slot 2026-08-06T12:40:00Z encodé en « seconds since 2000-01-01 ».
_SLOT = dt.datetime(2026, 8, 6, 12, 40, 0)
_SECS = (_SLOT - dt.datetime(2000, 1, 1)).total_seconds()


@pytest.fixture()
def config():
    return load_config("config/params.toml")


def _make_nc(path, *, group="ListProduct", conf_name="confidence"):
    """Fixture : 3 pixels — 2 dans le bbox France, 1 hors (lat 10.0 < 41)."""
    ds = netCDF4.Dataset(str(path), "w")
    g = ds.createGroup(group) if group else ds
    g.createDimension("pixel", 3)
    lat = g.createVariable("latitude", "f4", ("pixel",))
    lon = g.createVariable("longitude", "f4", ("pixel",))
    frp = g.createVariable("fire_radiative_power", "f4", ("pixel",))
    conf = g.createVariable(conf_name, "i4", ("pixel",))
    t = g.createVariable("time", "f8", ("pixel",))
    t.units = "seconds since 2000-01-01T00:00:00"
    t.calendar = "standard"
    lat[:] = [44.7, 48.0, 10.0]     # Gironde, Île-de-France, (hors France : lat 10)
    lon[:] = [-1.0, 2.0, 2.0]
    frp[:] = [12.5, 30.0, 5.0]
    conf[:] = [1, 2, 1]
    t[:] = [_SECS, _SECS, _SECS]
    ds.close()


def test_extraction_et_filtre_bbox(tmp_path, config):
    p = tmp_path / "fir.nc"
    _make_nc(p)
    pixels = mtg_netcdf.parse_listproduct(p, config, bbox=config["mtg"]["bbox"])
    assert len(pixels) == 2                      # le 3e (lat 10) est hors bbox
    assert pixels[0]["lat"] == pytest.approx(44.7, abs=1e-4)
    assert pixels[0]["lon"] == pytest.approx(-1.0, abs=1e-4)
    assert pixels[0]["frp_mw"] == pytest.approx(12.5, abs=1e-3)
    assert pixels[0]["confidence"] == "1"        # entier propre, texte brut
    assert pixels[1]["confidence"] == "2"


def test_decode_temps_cf(tmp_path, config):
    p = tmp_path / "fir.nc"
    _make_nc(p)
    pixels = mtg_netcdf.parse_listproduct(p, config, bbox=config["mtg"]["bbox"])
    assert pixels[0]["acq_at"] == "2026-08-06T12:40:00Z"


def test_lecture_depuis_octets(tmp_path, config):
    """Le fetcher passe des OCTETS téléchargés : parsing en mémoire (memory=)."""
    p = tmp_path / "fir.nc"
    _make_nc(p)
    data = p.read_bytes()
    pixels = mtg_netcdf.parse_listproduct(data, config, bbox=config["mtg"]["bbox"])
    assert len(pixels) == 2
    assert pixels[1]["frp_mw"] == pytest.approx(30.0, abs=1e-3)


def test_sans_bbox_tout_est_pris(tmp_path, config):
    p = tmp_path / "fir.nc"
    _make_nc(p)
    assert len(mtg_netcdf.parse_listproduct(p, config)) == 3


def test_default_acq_at_si_pas_de_temps(tmp_path, config):
    """Sans variable temps exploitable, on retombe sur l'heure du slot fournie par le listing."""
    ds = netCDF4.Dataset(str(tmp_path / "no_time.nc"), "w")
    g = ds.createGroup("ListProduct")
    g.createDimension("pixel", 1)
    g.createVariable("latitude", "f4", ("pixel",))[:] = [44.7]
    g.createVariable("longitude", "f4", ("pixel",))[:] = [-1.0]
    ds.close()
    pixels = mtg_netcdf.parse_listproduct(
        tmp_path / "no_time.nc", config, bbox=config["mtg"]["bbox"],
        default_acq_at="2026-08-06T12:50:00Z",
    )
    assert pixels[0]["acq_at"] == "2026-08-06T12:50:00Z"
    assert pixels[0]["frp_mw"] is None           # pas de variable FRP → None


def test_tolerance_noms_variables(tmp_path, config):
    """Un nom candidat alternatif (fire_confidence) est reconnu (config = liste de candidats)."""
    p = tmp_path / "alt.nc"
    _make_nc(p, conf_name="fire_confidence")
    pixels = mtg_netcdf.parse_listproduct(p, config, bbox=config["mtg"]["bbox"])
    assert pixels[0]["confidence"] == "1"


def test_lat_lon_manquants_leve(tmp_path, config):
    ds = netCDF4.Dataset(str(tmp_path / "bad.nc"), "w")
    g = ds.createGroup("ListProduct")
    g.createDimension("pixel", 1)
    g.createVariable("fire_radiative_power", "f4", ("pixel",))[:] = [1.0]
    ds.close()
    with pytest.raises(mtg_netcdf.MtgNetcdfError):
        mtg_netcdf.parse_listproduct(tmp_path / "bad.nc", config)


def test_groupe_absent_repli_racine(tmp_path, config):
    """Si le groupe ListProduct n'existe pas, on lit à la racine (tolérant)."""
    p = tmp_path / "root.nc"
    _make_nc(p, group=None)  # variables à la racine
    pixels = mtg_netcdf.parse_listproduct(p, config, bbox=config["mtg"]["bbox"])
    assert len(pixels) == 2
