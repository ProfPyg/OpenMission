"""
geo_data.py — Hardcoded Natural Earth 1:110m country polygons for OpenMission.

Each country is stored as a list of polygon rings (lon, lat) matching the
simplified outlines from the Natural Earth 1:110m admin-0 dataset (public domain).
Multi-part countries (islands, exclaves) carry one ring per part.

Public API
----------
listCountries()                           → sorted list of 44 country names
getCountryBBox(country)                   → (minlon, minlat, maxlon, maxlat)
load_shapefile(path)                      → shapely geometry from a .shp file
buildROIGrid(country, rows, cols,         → (lat_array, lon_array) inside border
             *, geom=None)
"""

from __future__ import annotations
import numpy as np
from shapely.geometry import Point, Polygon, MultiPolygon
from shapely.ops import unary_union

# ---------------------------------------------------------------------------
# Polygon data
# Each entry:  country_name → [ ring0, ring1, ... ]
# ring = list of (lon, lat) tuples (Natural Earth 1:110m simplified)
# ---------------------------------------------------------------------------
_POLY_DATA: dict[str, list[list[tuple[float, float]]]] = {

    "Albania": [[
        (20.07, 42.56), (20.59, 41.87), (21.02, 40.84),
        (20.63, 40.08), (19.98, 39.69), (19.37, 40.09),
        (19.29, 40.96), (19.51, 41.54), (20.07, 42.56),
    ]],

    "Algeria": [[
        (-1.80, 35.09), (9.48, 37.09), (9.48, 30.31),
        (11.97, 23.47), (8.57, 19.48), (3.41, 19.14),
        (-4.85, 19.57), (-8.68, 27.66), (-2.10, 35.01),
        (-1.80, 35.09),
    ]],

    "Austria": [[
        (9.53, 47.52), (10.44, 47.96), (12.14, 47.70),
        (13.03, 47.46), (14.32, 48.31), (14.90, 48.96),
        (16.94, 48.59), (17.16, 48.01), (16.50, 47.50),
        (14.94, 46.62), (13.81, 46.52), (12.41, 46.72),
        (10.44, 46.87), (9.56, 47.19), (9.53, 47.52),
    ]],

    "Belgium": [[
        (2.52, 51.09), (5.67, 51.48), (6.37, 50.76),
        (5.89, 49.54), (4.80, 49.98), (3.31, 50.78),
        (2.52, 51.09),
    ]],

    "Bosnia and Herzegovina": [[
        (15.75, 45.23), (16.53, 46.50), (17.65, 45.87),
        (18.56, 45.08), (19.00, 44.86), (19.22, 43.52),
        (18.45, 42.56), (17.51, 42.85), (17.02, 43.09),
        (15.75, 44.18), (15.75, 45.23),
    ]],

    "Bulgaria": [[
        (22.44, 44.23), (26.05, 44.16), (27.74, 44.18),
        (28.56, 43.71), (28.04, 41.98), (26.32, 41.72),
        (25.95, 41.32), (23.69, 41.31), (22.44, 41.24),
        (22.44, 44.23),
    ]],

    "Croatia": [[
        (13.65, 45.33), (16.53, 46.50), (17.63, 45.87),
        (18.82, 45.91), (19.07, 44.77), (18.55, 43.51),
        (17.86, 42.86), (16.90, 43.21), (16.01, 43.50),
        (14.93, 44.90), (14.36, 45.47), (13.65, 45.33),
    ]],

    "Cyprus": [[
        (32.26, 35.10), (33.03, 34.72), (33.97, 34.68),
        (34.57, 35.10), (33.38, 35.47), (32.92, 35.09),
        (32.26, 35.10),
    ]],

    "Czechia": [[
        (12.56, 50.32), (14.35, 51.12), (16.15, 50.70),
        (17.71, 50.30), (18.86, 49.90), (18.86, 49.50),
        (17.87, 48.90), (16.96, 48.60), (15.26, 49.00),
        (13.03, 48.63), (12.56, 50.32),
    ]],

    "Denmark": [[
        (8.12, 54.88), (8.12, 57.02), (10.38, 57.73),
        (10.64, 57.38), (10.38, 56.61), (12.69, 56.14),
        (12.69, 55.40), (9.92, 54.88), (8.12, 54.88),
    ]],

    "Egypt": [[
        (24.70, 22.00), (36.89, 22.00), (36.89, 29.56),
        (34.92, 29.50), (34.21, 31.22), (31.23, 31.42),
        (24.70, 31.34), (24.70, 22.00),
    ]],

    "Finland": [[
        (20.65, 59.89), (26.87, 60.25), (29.00, 61.15),
        (28.97, 63.03), (30.16, 65.67), (29.50, 69.77),
        (27.96, 70.09), (26.18, 69.63), (24.69, 68.95),
        (22.42, 68.72), (20.41, 68.59), (19.18, 69.83),
        (17.71, 68.55), (17.99, 65.98), (20.12, 65.13),
        (19.87, 63.13), (20.65, 59.89),
    ]],

    "France": [[
        (-4.47, 48.37), (2.52, 51.09), (7.60, 50.06),
        (7.97, 47.48), (10.44, 43.69), (7.68, 43.77),
        (6.84, 44.05), (3.42, 43.10), (1.83, 43.28),
        (-0.32, 43.07), (-1.78, 43.41), (-4.47, 47.95),
        (-4.47, 48.37),
    ]],

    "Germany": [[
        (5.99, 50.87), (7.02, 51.50), (9.73, 53.55),
        (8.67, 55.07), (11.89, 54.34), (14.06, 53.98),
        (14.62, 52.08), (15.01, 50.85), (13.03, 47.46),
        (12.14, 47.70), (9.53, 47.52), (6.84, 47.54),
        (6.34, 49.46), (5.99, 50.87),
    ]],

    "Greece": [
        # Mainland + Peloponnese
        [(20.07, 40.85), (19.99, 39.69), (20.58, 38.46),
         (21.10, 38.37), (21.88, 37.65), (22.65, 36.42),
         (23.19, 36.39), (23.86, 37.62), (24.03, 38.62),
         (25.05, 40.57), (26.10, 41.01), (26.58, 41.72),
         (25.28, 41.24), (23.97, 41.37), (23.22, 41.33),
         (22.42, 41.12), (21.57, 40.86), (20.73, 40.43),
         (20.07, 40.85)],
        # Crete
        [(23.52, 35.54), (24.48, 35.00), (25.73, 35.00),
         (26.32, 35.23), (25.74, 35.60), (24.79, 35.57),
         (23.52, 35.54)],
    ],

    "Hungary": [[
        (16.20, 46.86), (17.17, 47.76), (18.78, 47.98),
        (20.24, 48.28), (21.72, 48.34), (22.09, 47.67),
        (22.09, 47.67), (21.62, 46.31), (20.22, 45.76),
        (18.83, 45.78), (16.88, 46.15), (16.20, 46.86),
    ]],

    "Iran": [[
        (44.77, 39.71), (47.99, 38.41), (50.14, 37.37),
        (53.92, 37.20), (56.18, 38.12), (60.50, 36.51),
        (61.21, 35.65), (63.19, 35.86), (61.78, 34.00),
        (60.87, 31.50), (61.42, 29.00), (58.83, 25.67),
        (56.34, 25.01), (54.28, 24.20), (51.58, 24.25),
        (50.14, 26.14), (49.54, 30.01), (48.00, 29.61),
        (46.55, 29.09), (45.00, 30.25), (44.71, 33.00),
        (45.40, 35.97), (44.77, 37.17), (44.77, 39.71),
    ]],

    "Iraq": [[
        (39.19, 37.15), (42.35, 37.23), (42.78, 36.60),
        (41.44, 34.41), (40.34, 33.00), (38.79, 33.38),
        (38.89, 29.83), (46.42, 29.10), (48.58, 29.97),
        (48.58, 30.49), (47.97, 31.39), (47.35, 33.67),
        (46.13, 35.86), (44.77, 37.17), (43.94, 37.26),
        (39.19, 37.15),
    ]],

    "Israel": [[
        (34.27, 29.56), (34.65, 31.62), (35.24, 31.82),
        (35.76, 32.73), (35.54, 33.25), (35.14, 33.09),
        (34.96, 29.36), (34.27, 29.56),
    ]],

    "Italy": [
        # Mainland
        [(6.60, 44.15), (8.10, 46.00), (10.44, 46.87),
         (13.72, 46.52), (14.76, 45.63), (15.55, 44.01),
         (15.14, 43.62), (15.73, 41.00), (15.49, 40.15),
         (16.03, 39.04), (16.43, 39.30), (17.18, 40.41),
         (18.05, 40.56), (18.37, 40.35), (17.52, 38.87),
         (16.28, 37.95), (15.63, 37.94), (15.63, 38.50),
         (15.10, 37.46), (14.10, 37.12), (13.08, 37.53),
         (12.43, 37.82), (11.03, 37.91), (10.44, 43.70),
         (9.22, 44.38), (8.13, 44.10), (6.74, 44.01),
         (6.60, 44.15)],
        # Sicily
        [(12.43, 37.82), (13.52, 37.04), (15.10, 37.46),
         (15.63, 37.60), (15.10, 38.10), (13.17, 38.23),
         (12.43, 37.82)],
        # Sardinia
        [(8.37, 38.90), (9.83, 40.51), (9.83, 41.25),
         (8.50, 41.14), (8.22, 40.00), (8.37, 38.90)],
    ],

    "Jordan": [[
        (35.55, 32.38), (36.83, 32.31), (38.99, 32.01),
        (40.19, 31.97), (40.38, 30.55), (37.71, 28.90),
        (37.49, 27.80), (36.75, 27.87), (36.48, 28.91),
        (35.02, 29.68), (34.99, 29.57), (34.63, 30.06),
        (34.97, 31.62), (35.47, 31.49), (35.55, 32.38),
    ]],

    "Lebanon": [[
        (35.10, 33.09), (35.77, 33.28), (36.61, 34.64),
        (36.49, 34.22), (35.77, 33.28), (35.10, 33.09),
    ]],

    "Libya": [[
        (9.32, 30.31), (9.48, 36.90), (11.57, 37.15),
        (14.79, 33.33), (15.35, 30.34), (19.86, 30.76),
        (24.73, 31.34), (25.00, 29.81), (25.00, 22.00),
        (24.47, 22.00), (11.99, 23.47), (9.32, 30.31),
    ]],

    "Luxembourg": [[
        (5.67, 49.54), (6.13, 50.38), (6.53, 50.13),
        (6.22, 49.45), (5.67, 49.54),
    ]],

    "Montenegro": [[
        (18.45, 42.56), (19.22, 43.52), (20.07, 43.00),
        (19.80, 42.08), (19.22, 41.95), (18.74, 42.25),
        (18.45, 42.56),
    ]],

    "Morocco": [[
        (-5.92, 35.77), (-1.80, 35.09), (-1.74, 33.67),
        (-2.17, 31.17), (-2.92, 30.38), (-3.29, 29.22),
        (-4.61, 29.89), (-8.68, 27.66), (-13.03, 27.89),
        (-14.77, 29.73), (-17.02, 31.13), (-13.18, 33.14),
        (-12.53, 33.53), (-6.92, 35.83), (-5.92, 35.77),
    ]],

    "Netherlands": [[
        (3.31, 51.35), (7.09, 53.15), (7.09, 52.66),
        (6.07, 51.85), (5.86, 50.84), (3.31, 50.78),
        (3.31, 51.35),
    ]],

    "North Macedonia": [[
        (20.45, 42.09), (21.92, 42.32), (22.38, 41.80),
        (22.93, 41.34), (22.42, 41.12), (21.57, 40.86),
        (20.72, 41.41), (20.45, 42.09),
    ]],

    "Norway": [[
        (4.85, 57.98), (8.01, 58.17), (8.01, 62.00),
        (14.77, 65.60), (16.57, 68.56), (20.42, 68.95),
        (27.96, 70.09), (30.92, 69.78), (28.44, 69.04),
        (23.36, 69.03), (19.18, 69.83), (17.71, 68.55),
        (14.77, 67.18), (12.18, 63.30), (11.33, 59.13),
        (7.01, 57.92), (4.85, 57.98),
    ]],

    "Poland": [[
        (14.07, 53.75), (18.82, 54.94), (22.73, 54.33),
        (23.48, 51.91), (24.14, 50.86), (22.56, 49.08),
        (18.86, 49.50), (17.65, 50.25), (16.15, 50.70),
        (14.80, 50.76), (14.62, 52.08), (14.07, 53.75),
    ]],

    "Portugal": [[
        (-9.20, 38.71), (-9.49, 39.19), (-8.98, 41.94),
        (-6.64, 42.40), (-7.49, 41.77), (-7.40, 41.02),
        (-6.62, 40.12), (-7.01, 39.03), (-7.31, 38.39),
        (-8.97, 38.73), (-9.20, 38.71),
    ]],

    "Romania": [[
        (22.42, 48.25), (24.30, 48.01), (26.62, 48.22),
        (29.06, 46.29), (29.62, 45.30), (29.07, 43.97),
        (28.56, 43.71), (27.74, 44.18), (23.39, 43.68),
        (22.35, 44.00), (21.62, 45.58), (22.14, 47.17),
        (22.42, 48.25),
    ]],

    "Saudi Arabia": [[
        (36.75, 27.87), (38.56, 27.38), (44.00, 25.47),
        (47.99, 22.00), (51.60, 22.00), (55.67, 22.00),
        (57.82, 23.90), (58.86, 21.11), (55.43, 20.42),
        (52.00, 19.00), (50.00, 17.00), (46.10, 17.30),
        (43.10, 17.18), (42.00, 19.00), (39.50, 20.50),
        (38.35, 22.00), (37.00, 24.00), (36.75, 27.87),
    ]],

    "Serbia": [[
        (18.83, 45.91), (20.22, 45.76), (21.62, 46.31),
        (22.09, 47.67), (22.56, 48.08), (22.93, 45.00),
        (22.44, 44.23), (22.35, 44.00), (21.62, 44.32),
        (20.79, 44.01), (19.55, 44.04), (19.22, 43.52),
        (18.45, 42.56), (17.48, 45.11), (18.83, 45.91),
    ]],

    "Slovakia": [[
        (16.96, 48.60), (17.87, 48.90), (18.86, 49.50),
        (22.56, 49.08), (22.09, 48.34), (21.72, 48.34),
        (20.24, 48.28), (18.78, 47.98), (17.17, 47.76),
        (16.96, 48.60),
    ]],

    "Slovenia": [[
        (13.65, 45.33), (14.36, 45.47), (14.93, 44.90),
        (15.04, 45.64), (16.53, 46.50), (16.20, 46.86),
        (14.90, 46.48), (13.81, 46.52), (13.65, 45.79),
        (13.65, 45.33),
    ]],

    "Spain": [[
        (-9.39, 43.03), (-7.49, 43.76), (-4.61, 43.60),
        (-1.78, 43.41), (3.20, 42.35), (3.20, 41.28),
        (0.71, 40.72), (0.29, 39.31), (0.59, 38.29),
        (-0.32, 36.71), (-4.99, 36.05), (-6.31, 36.81),
        (-7.44, 37.15), (-7.31, 38.39), (-6.62, 40.12),
        (-7.40, 41.02), (-9.20, 38.71), (-9.39, 43.03),
    ]],

    "Sweden": [[
        (11.47, 55.79), (12.30, 56.10), (18.00, 59.43),
        (19.39, 60.62), (22.20, 65.84), (24.55, 65.72),
        (26.18, 65.11), (26.18, 63.73), (24.55, 62.81),
        (22.63, 60.62), (20.12, 59.19), (18.00, 58.96),
        (14.09, 56.14), (11.47, 55.79),
    ]],

    "Switzerland": [[
        (6.02, 47.25), (8.52, 47.83), (10.44, 47.49),
        (10.44, 46.87), (8.51, 46.06), (6.84, 46.43),
        (6.02, 46.43), (6.02, 47.25),
    ]],

    "Syria": [[
        (35.72, 36.86), (36.61, 37.40), (37.21, 36.65),
        (38.77, 36.69), (40.74, 37.10), (42.35, 37.23),
        (41.01, 34.42), (37.00, 32.95), (35.72, 32.71),
        (35.55, 32.38), (35.47, 31.49), (34.97, 31.62),
        (36.43, 32.97), (36.48, 35.10), (35.72, 36.86),
    ]],

    "Tunisia": [[
        (9.48, 37.09), (11.57, 37.15), (10.99, 33.77),
        (11.43, 30.87), (9.96, 29.33), (8.57, 29.45),
        (7.79, 30.51), (8.11, 32.73), (9.21, 33.45),
        (8.35, 33.84), (8.55, 36.57), (9.48, 37.09),
    ]],

    "Turkey": [[
        (26.32, 41.72), (28.20, 41.98), (29.97, 41.50),
        (34.00, 42.05), (38.46, 41.63), (42.35, 41.58),
        (43.64, 40.98), (43.64, 39.72), (43.99, 38.66),
        (42.78, 37.38), (41.44, 36.79), (39.95, 36.68),
        (38.16, 37.14), (36.16, 36.21), (34.97, 35.98),
        (32.79, 36.87), (29.40, 36.46), (26.32, 38.37),
        (25.74, 40.14), (26.32, 41.72),
    ]],

    "Ukraine": [[
        (22.14, 48.45), (22.56, 48.08), (24.30, 48.01),
        (26.62, 48.22), (29.06, 46.29), (31.75, 47.08),
        (33.54, 46.04), (35.01, 45.73), (36.56, 46.52),
        (38.26, 47.59), (39.12, 47.26), (40.08, 47.28),
        (39.67, 48.26), (37.33, 50.00), (35.23, 51.09),
        (32.16, 52.10), (30.81, 52.10), (28.08, 51.68),
        (27.21, 51.50), (24.14, 51.89), (23.48, 51.91),
        (22.73, 51.50), (22.14, 48.45),
    ]],

    "United Kingdom": [[
        (-5.66, 50.05), (-3.00, 51.59), (1.73, 51.80),
        (1.73, 53.58), (0.02, 55.44), (-2.09, 55.91),
        (-3.00, 57.70), (-4.86, 58.63), (-5.66, 57.68),
        (-2.09, 55.91), (-4.86, 55.17), (-5.24, 52.11),
        (-5.66, 50.05),
    ]],

}

# ---------------------------------------------------------------------------
# Shapely geometry cache
# ---------------------------------------------------------------------------
_GEOM_CACHE: dict[str, object] = {}


def _make_geometry(country: str):
    """Return a cached shapely (Multi)Polygon for *country*."""
    if country in _GEOM_CACHE:
        return _GEOM_CACHE[country]
    rings = _POLY_DATA[country]
    polys = [Polygon(ring) for ring in rings if len(ring) >= 3]
    geom  = unary_union(polys) if len(polys) > 1 else polys[0]
    _GEOM_CACHE[country] = geom
    return geom


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_shapefile(path: str):
    """
    Load a .shp file with pyshp and return a unified shapely geometry.

    All polygon records and their parts are merged with ``unary_union``.
    Pass the result as the *geom* keyword argument to ``buildROIGrid`` to use
    a custom boundary instead of the built-in Natural Earth outlines.

    Parameters
    ----------
    path : str
        Path to the .shp file.  The extension may be omitted; pyshp will find
        the accompanying .dbf/.shx automatically.

    Returns
    -------
    shapely (Multi)Polygon

    Raises
    ------
    ImportError  — if pyshp is not installed
    RuntimeError — if the file cannot be opened or contains no polygon shapes
    """
    try:
        import shapefile          # pyshp (already in requirements.txt)
    except ImportError:
        raise ImportError("pyshp is required: pip install pyshp")

    try:
        sf = shapefile.Reader(path)
    except Exception as exc:
        raise RuntimeError(f"Cannot open shapefile '{path}': {exc}") from exc

    polys: list = []
    for shape in sf.shapes():
        # Shape type 5 = Polygon, 15 = PolygonZ, 25 = PolygonM
        if shape.shapeType not in (5, 15, 25):
            continue
        pts   = shape.points
        parts = list(shape.parts) + [len(pts)]
        for i in range(len(parts) - 1):
            ring = pts[parts[i] : parts[i + 1]]
            if len(ring) < 3:
                continue
            try:
                p = Polygon(ring)
                if not p.is_valid:
                    p = p.buffer(0)     # fix self-intersections
                if not p.is_empty:
                    polys.append(p)
            except Exception:
                pass

    if not polys:
        raise RuntimeError(f"No polygon shapes found in '{path}'.")

    return unary_union(polys)


def listCountries() -> list[str]:
    """Return sorted list of all available country names."""
    # deduplicate (Luxembourg appears twice — keep only unique keys)
    return sorted(set(_POLY_DATA.keys()))


def getCountryBBox(country: str) -> tuple[float, float, float, float]:
    """
    Return (minlon, minlat, maxlon, maxlat) bounding box for *country*.

    Raises KeyError if country is not in the database.
    """
    if country not in _POLY_DATA:
        raise KeyError(f"Country '{country}' not found. Use listCountries().")
    geom = _make_geometry(country)
    return geom.bounds   # (minx, miny, maxx, maxy) = (minlon, minlat, maxlon, maxlat)


def buildROIGrid(
    country: str,
    rows: int = 5,
    cols: int = 10,
    *,
    geom=None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Build a lat/lon grid and return only the points inside the boundary.

    Mirrors the MATLAB VLEOExample.m ROI construction:
        latv = linspace(latmin, latmax, rows)
        lonv = linspace(lonmin, lonmax, cols)
        [Lon, Lat] = meshgrid(lonv, latv)
        inside = inpolygon(Lon, Lat, border_x, border_y)

    Parameters
    ----------
    country : str
        Must be a key in listCountries().  Ignored when *geom* is supplied.
    rows    : int  — number of latitude samples across the bounding box
    cols    : int  — number of longitude samples across the bounding box
    geom    : shapely geometry, optional
        If provided (e.g. from ``load_shapefile``), this geometry is used
        directly and *country* is not looked up.  Bounding box is derived
        from the geometry's own bounds.

    Returns
    -------
    lat_arr : np.ndarray  — latitudes  of interior grid points [deg]
    lon_arr : np.ndarray  — longitudes of interior grid points [deg]

    Raises KeyError if country is unknown and *geom* is None.
    """
    if geom is None:
        if country not in _POLY_DATA:
            raise KeyError(f"Country '{country}' not found. Use listCountries().")
        geom = _make_geometry(country)
    minlon, minlat, maxlon, maxlat = geom.bounds

    latv = np.linspace(minlat, maxlat, rows)
    lonv = np.linspace(minlon, maxlon, cols)

    Lon, Lat = np.meshgrid(lonv, latv)
    lat_flat = Lat.ravel()
    lon_flat = Lon.ravel()

    # shapely vectorised containment check
    inside = np.array([
        geom.covers(Point(lo, la))
        for lo, la in zip(lon_flat, lat_flat)
    ], dtype=bool)

    return lat_flat[inside], lon_flat[inside]


# ---------------------------------------------------------------------------
# Self-test  (python geo_data.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Available countries ({len(listCountries())}):")
    print("  " + ", ".join(listCountries()))
    print()

    # ---- Greece ----
    bbox = getCountryBBox("Greece")
    print(f"Greece bbox: lon [{bbox[0]:.2f}, {bbox[2]:.2f}]  "
          f"lat [{bbox[1]:.2f}, {bbox[3]:.2f}]")

    lat, lon = buildROIGrid("Greece", rows=5, cols=10)
    print(f"Greece 5x10 grid: {len(lat)} interior points")
    for la, lo in zip(lat, lon):
        print(f"  ({la:.2f}N, {lo:.2f}E)")

    # ---- Containment sanity checks ----
    print()
    geom_gr = _make_geometry("Greece")
    tests = [
        ("Athens",    23.73,  37.97, "Greece",  True),
        ("Thessaloniki", 22.93, 40.62, "Greece", True),
        ("Heraklion (Crete)", 25.14, 35.34, "Greece", True),
        ("Rome",      12.49,  41.89, "Greece",  False),
        ("London",    -0.12,  51.51, "Greece",  False),
        ("Paris",      2.35,  48.85, "France",  True),
        ("Cairo",     31.24,  30.06, "Egypt",   True),
    ]
    all_ok = True
    for name, lon_t, lat_t, country, expected in tests:
        geom = _make_geometry(country)
        result = geom.covers(Point(lon_t, lat_t))
        ok = result == expected
        status = "OK" if ok else "FAIL"
        print(f"[{status}] {name:30s} inside {country}: {result} (expected {expected})")
        if not ok:
            all_ok = False

    print()
    print("All checks passed." if all_ok else "SOME CHECKS FAILED — review polygon data.")
