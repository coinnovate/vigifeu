"""Parsing du netCDF FIR 0682 — grille géostationnaire (Spec 07 §2, étape 3, révisé prod).

Fixture synthétique : une petite grille en VRAIE projection géostationnaire (params du 0682 réel),
avec des pixels feu placés par déprojection inverse. On couvre : déprojection x,y → lat/lon, filtrage
bbox, classes-feu configurables, probabilité, heure depuis l'attribut global, dézippage du SIP,
et l'erreur si une variable de grille manque.
"""

from __future__ import annotations

import io
import zipfile

import netCDF4
import numpy as np
import pytest
from pyproj import CRS, Transformer

from vigifeu.ingest import mtg_netcdf
from vigifeu.model.db import load_config

_H = 3.57864e7
_A, _B = 6378137.0, 6356752.0
_GEOS = CRS.from_proj4(
    f"+proj=geos +h={_H} +lon_0=0 +a={_A} +b={_B} +sweep=y +units=m +no_defs +type=crs"
)
_FWD = Transformer.from_crs("EPSG:4326", _GEOS, always_xy=True)

FRANCE = (-1.0, 44.7)    # Gironde
AFRIQUE = (15.0, 5.0)    # sur le disque, hors bbox France


@pytest.fixture()
def config():
    return load_config("config/params.toml")


def _rad(lon, lat):
    """(lon,lat) → (x_rad, y_rad) tels que stockés dans le netCDF (mètres / hauteur satellite)."""
    xm, ym = _FWD.transform(lon, lat)
    return xm / _H, ym / _H


def _make_grid(path):
    """3×3 grille geos : pixel (0,0)=France classe 3, (1,1)=Afrique classe 2, reste 0/4."""
    xa, ya = _rad(*FRANCE)
    xb, yb = _rad(*AFRIQUE)
    ds = netCDF4.Dataset(str(path), "w")
    ds.time_coverage_start = "20260806171000"
    ds.createDimension("number_of_rows", 3)
    ds.createDimension("number_of_columns", 3)
    proj = ds.createVariable("mtg_geos_projection", "i4")
    proj.perspective_point_height = np.float32(_H)
    proj.semi_major_axis = np.float32(_A)
    proj.semi_minor_axis = np.float32(_B)
    proj.longitude_of_projection_origin = np.float32(0.0)
    proj.sweep_angle_axis = "y"
    ds.createVariable("x", "f8", ("number_of_columns",))[:] = [xa, xb, xa]
    ds.createVariable("y", "f8", ("number_of_rows",))[:] = [ya, yb, ya]
    fr = ds.createVariable("fire_result", "i1", ("number_of_rows", "number_of_columns"))
    grid = np.zeros((3, 3), dtype="i1")
    grid[0, 0] = 3          # France, haute confiance
    grid[1, 1] = 2          # Afrique, confiance moyenne
    grid[2, 2] = 4          # hors-disque/non traité (jamais « feu »)
    fr[:] = grid
    prob = ds.createVariable("fire_probability", "f4", ("number_of_rows", "number_of_columns"))
    pg = np.zeros((3, 3), dtype="f4")
    pg[0, 0] = 0.87
    prob[:] = pg
    ds.close()


def test_deprojection_et_bbox(tmp_path, config):
    p = tmp_path / "fir.nc"
    _make_grid(p)
    pix = mtg_netcdf.parse_fir(p, config, bbox=config["mtg"]["bbox"])
    assert len(pix) == 1                                  # seule la France passe le bbox
    assert pix[0]["lon"] == pytest.approx(-1.0, abs=1e-3)
    assert pix[0]["lat"] == pytest.approx(44.7, abs=1e-3)
    assert pix[0]["confidence"] == "3"                    # classe de détection
    assert pix[0]["probability"] == pytest.approx(0.87, abs=1e-2)
    assert pix[0]["frp_mw"] is None                       # le 0682 n'a PAS de FRP


def test_heure_depuis_attribut_global(tmp_path, config):
    p = tmp_path / "fir.nc"
    _make_grid(p)
    pix = mtg_netcdf.parse_fir(p, config, bbox=config["mtg"]["bbox"])
    assert pix[0]["acq_at"] == "2026-08-06T17:10:00Z"     # time_coverage_start


def test_sans_bbox_deux_pixels(tmp_path, config):
    p = tmp_path / "fir.nc"
    _make_grid(p)
    pix = mtg_netcdf.parse_fir(p, config)                 # France + Afrique (le 4 est exclu)
    assert len(pix) == 2


def test_classes_configurable(tmp_path, config):
    p = tmp_path / "fir.nc"
    _make_grid(p)
    config["mtg"]["netcdf"]["fire_classes"] = [3]         # seule la haute confiance
    pix = mtg_netcdf.parse_fir(p, config)
    assert len(pix) == 1 and pix[0]["confidence"] == "3"


def test_lecture_depuis_zip_sip(tmp_path, config):
    """Le Data Store livre un ZIP (SIP) : parse_fir en extrait le .nc."""
    p = tmp_path / "fir.nc"
    _make_grid(p)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(p, arcname="MTI1+FCI-2-FIR.nc")
        z.writestr("manifest.xml", "<x/>")
    pix = mtg_netcdf.parse_fir(buf.getvalue(), config, bbox=config["mtg"]["bbox"])
    assert len(pix) == 1 and pix[0]["confidence"] == "3"


def test_variable_absente_leve(tmp_path, config):
    ds = netCDF4.Dataset(str(tmp_path / "bad.nc"), "w")
    ds.createDimension("n", 1)
    ds.createVariable("x", "f8", ("n",))
    ds.close()
    with pytest.raises(mtg_netcdf.MtgNetcdfError):
        mtg_netcdf.parse_fir(tmp_path / "bad.nc", config)
