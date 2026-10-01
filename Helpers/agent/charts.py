"""charts.py -- Plotly figures from datasets STK produced (via save_data).

Figures are built from fixed templates; the model only picks the kind and
the columns, so drawing costs no tokens however many points there are.
"""

from __future__ import annotations

import math
import re
from datetime import datetime

import plotly.graph_objects as go

_UTCG = re.compile(r"^\s*(\d{1,2} \w{3} \d{4} \d{2}:\d{2}:\d{2})(?:\.(\d+))?\s*$")
EARTH_R = 6378.137
PALETTE = ["#d9c9a3", "#8f9a5c", "#b08a5a", "#9fae8a", "#efe8da", "#c7a574", "#6e7f4a"]


def _as_time(values):
    """STK UTCG strings -> datetimes; anything else is returned unchanged."""
    out = []
    for v in values:
        m = _UTCG.match(str(v))
        if not m:
            return values
        frac = (m.group(2) or "0")[:6].ljust(6, "0")
        out.append(datetime.strptime("%s.%s" % (m.group(1), frac), "%d %b %Y %H:%M:%S.%f"))
    return out


_TEXTURE_CACHE = {}


def _tint(im):
    """The real surface, re-coloured into Pronoia's Earth palette: shadows go
    to the page background, mid-tones to olive, highlights to sand. Keeps
    every crater and coastline, loses the photo colours."""
    from PIL import ImageOps
    g = ImageOps.autocontrast(im.convert("L").resize((2048, 1024)), cutoff=1)
    return ImageOps.colorize(g, black="#0c0e0b", mid="#3c4329", white="#8f8466")


def body_texture(body):
    """A dark equirectangular image of ``body`` as a data URI, or None.

    From static/bodies/<Body>.jpg (Pronoia's texture pack) if present,
    otherwise from the user's OWN STK install (STK ships textures for the Moon,
    Mars and other bodies), so nothing is downloaded or redistributed. The
    largest roughly 2:1 image whose name contains the body is used, shrunk
    to 2048 px and darkened so the ground track stays readable.
    """
    import base64, glob, io, os
    key = body.lower()
    if key in _TEXTURE_CACHE:
        return _TEXTURE_CACHE[key]
    uri = None
    try:
        from PIL import Image, ImageEnhance
        here = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            "static", "bodies", body.strip().title() + ".jpg")
        if os.path.exists(here):          # Pronoia's own texture pack first
            with Image.open(here) as im:
                im = _tint(im)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=80)
            uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
            _TEXTURE_CACHE[key] = uri
            return uri
        roots = sorted(glob.glob(r"C:\Program Files\AGI\STK*")) + \
            sorted(glob.glob(r"C:\Program Files (x86)\AGI\STK*"))
        best = None
        for root in roots:
            for d, _, files in os.walk(os.path.join(root, "STKData")):
                for f in files:
                    fl = f.lower()
                    if key in fl and fl.endswith((".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")):
                        p = os.path.join(d, f)
                        try:
                            with Image.open(p) as im:
                                w, h = im.size
                        except Exception:
                            continue
                        if h and 1.8 <= w / h <= 2.2 and (best is None or w > best[0]):
                            best = (w, p)
        if best:
            with Image.open(best[1]) as im:
                im = _tint(im)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=80)
            uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        uri = None
    _TEXTURE_CACHE[key] = uri
    return uri


def _earth():
    u = [i * 2 * math.pi / 48 for i in range(49)]
    v = [i * math.pi / 24 for i in range(25)]
    x = [[EARTH_R * math.cos(a) * math.sin(b) for a in u] for b in v]
    y = [[EARTH_R * math.sin(a) * math.sin(b) for a in u] for b in v]
    z = [[EARTH_R * math.cos(b) for _ in u] for b in v]
    return go.Surface(x=x, y=y, z=z, showscale=False, opacity=0.85,
                      colorscale=[[0, "#1d4e89"], [1, "#2e86c1"]], hoverinfo="skip")


def build(spec, data):
    cols = data["columns"]
    kind = spec["kind"]
    fig = go.Figure()
    if kind == "contour":
        # a value over a REGULAR 2-D grid, drawn as contour lines -- the
        # porkchop plot (departure date x arrival date -> C3 / delta-v)
        xs_raw, ys_raw, vs = cols[spec["x"]], cols[spec["y"][0] if spec.get("y") else "y"], cols[spec["value"]]
        xu = sorted(set(xs_raw), key=lambda t: _as_time([t])[0] if isinstance(t, str) else t)
        yu = sorted(set(ys_raw), key=lambda t: _as_time([t])[0] if isinstance(t, str) else t)
        xi, yi = {v: i for i, v in enumerate(xu)}, {v: i for i, v in enumerate(yu)}
        z = [[None] * len(xu) for _ in yu]
        for xv, yv, vv in zip(xs_raw, ys_raw, vs):
            z[yi[yv]][xi[xv]] = vv
        vals = sorted(v for v in vs if v is not None)
        cap = spec.get("value_max") or (vals[int(0.6 * (len(vals) - 1))] if vals else 1)
        z = [[(v if v is not None and v <= cap else None) for v in row] for row in z]
        lo = vals[0] if vals else 0
        fig.add_contour(x=_as_time(xu), y=_as_time(yu), z=z, connectgaps=False,
                        contours={"start": lo, "end": cap, "size": (cap - lo) / 14 or 1,
                                  "coloring": "lines", "showlabels": True,
                                  "labelfont": {"size": 9, "color": "#a39d8e"}},
                        line={"width": 1.3}, showscale=True, showlegend=False,
                        colorscale=[[0, "#f3e7c4"], [0.35, "#c7a574"], [0.7, "#6e7f4a"], [1, "#3a4128"]],
                        colorbar={"title": spec.get("value_title") or spec["value"]})
        if vals:
            k = min(range(len(vs)), key=lambda n: vs[n] if vs[n] is not None else 1e99)
            fig.add_scatter(x=_as_time([xs_raw[k]]), y=_as_time([ys_raw[k]]), mode="markers+text",
                            text=["best %.2f" % vs[k]], textposition="top center", showlegend=False,
                            textfont={"color": "#f6f1e6", "size": 11},
                            marker={"symbol": "star", "size": 15, "color": "#f6f1e6",
                                    "line": {"width": 1, "color": "#0b0d0c"}})
    elif kind == "scatter" and spec.get("value"):
        # a value plotted as COLOUR over two variables -- e.g. a porkchop
        # (departure date x flight time -> C3) or cost vs revisit coloured by
        # altitude. Without the colour such a plot only shows where samples
        # were taken, which reads as noise.
        x = _as_time(cols[spec["x"]])
        y = cols[spec["y"][0]] if spec.get("y") else cols["y"]
        v = cols[spec["value"]]
        pairs = [(i, val) for i, val in enumerate(v) if val is not None]
        lo, hi = (min(p[1] for p in pairs), max(p[1] for p in pairs)) if pairs else (0, 1)
        fig.add_scatter(x=x, y=y, mode="markers", name=spec["value"], showlegend=False,
                        text=[str(val) for val in v],
                        hovertemplate="%{x}<br>%{y}<br>" + spec["value"] + " %{text}<extra></extra>",
                        marker={"color": v, "size": 8, "cmin": lo, "cmax": hi,
                                "colorscale": [[0, "#f3e7c4"], [0.35, "#b08a5a"],
                                               [0.7, "#5d6440"], [1, "#22261a"]],
                                "colorbar": {"title": spec.get("value_title") or spec["value"]}})
        if pairs:
            best = min(pairs, key=lambda p: p[1]) if spec.get("best", "min") == "min" \
                else max(pairs, key=lambda p: p[1])
            fig.add_scatter(x=[x[best[0]]], y=[y[best[0]]], mode="markers+text",
                            text=["best"], textposition="top center", showlegend=False,
                            marker={"symbol": "star", "size": 16, "color": "#f6f1e6",
                                    "line": {"width": 1, "color": "#0b0d0c"}})
    elif kind in ("line", "scatter", "bar"):
        x = _as_time(cols[spec["x"]]) if spec.get("x") else None
        for y in spec.get("y") or []:
            if kind == "bar":
                fig.add_bar(x=x, y=cols[y], name=y)
            else:
                fig.add_scatter(x=x, y=cols[y], name=y,
                                mode="lines" if kind == "line" else "markers")
    elif kind == "timeline":
        starts, stops = _as_time(cols[spec["start"]]), _as_time(cols[spec["stop"]])
        labels = cols[spec["label"]] if spec.get("label") else ["pass"] * len(starts)
        for s, e, lab in zip(starts, stops, labels):
            fig.add_scatter(x=[s, e], y=[str(lab), str(lab)], mode="lines",
                            line={"width": 14}, showlegend=False,
                            hovertext="%s → %s" % (s, e), hoverinfo="text")
    elif kind == "elements":
        from plotly.subplots import make_subplots
        ys = spec.get("y") or []
        x = _as_time(cols[spec["x"]]) if spec.get("x") else None
        fig = make_subplots(rows=len(ys), cols=1, shared_xaxes=True, vertical_spacing=0.04,
                            subplot_titles=ys)
        for i, y in enumerate(ys, 1):
            fig.add_scatter(x=x, y=cols[y], mode="lines", name=y, showlegend=False,
                            line={"width": 1.6, "color": PALETTE[(i - 1) % len(PALETTE)]}, row=i, col=1)
        fig.update_layout(height=max(360, 150 * len(ys)))
        fig.update_annotations(font_size=12, x=0, xanchor="left")
    elif kind in ("ground_track", "map_heat"):
        body = (spec.get("body") or "Earth").strip().title()
        lat, lon = cols[spec["lat"]], cols[spec["lon"]]
        if kind == "ground_track":
            # one line per satellite (label column, or an obvious sat/name column);
            # break each line where it wraps at +-180 so it does not streak across
            key = spec.get("label") or next((k for k in cols if k.lower() in ("sat", "satellite", "name", "object")), None)
            ids = cols[key] if key else [None] * len(lat)
            xs, ys = [], []
            for i, (la, lo) in enumerate(zip(lat, lon)):
                if i and (ids[i] != ids[i - 1] or abs(lo - lon[i - 1]) > 180):
                    xs.append(None); ys.append(None)
                xs.append(lo); ys.append(la)
            trace = dict(mode="lines", line={"width": 0.9, "color": "#f3eee2"}, name="ground track",
                         opacity=0.75)
        else:
            vals = cols[spec["value"]]
            xs, ys = lon, lat
            trace = dict(mode="markers", name=spec.get("value"), text=[str(v) for v in vals],
                         marker={"color": vals, "size": 9, "colorscale": [[0, "#2c3320"], [0.5, "#8f9a5c"], [1, "#d9c9a3"]],
                                 "colorbar": {"title": spec.get("value")}})
        tg = spec.get("targets") or []
        # every body -- Earth included -- is a lat/lon plot over its tinted texture
        if body == "Earth":
            try:
                import country_polygons
                bx, by = [], []
                for rings in country_polygons._POLY_DATA.values():
                    for ring in rings:
                        bx += [p[0] for p in ring] + [None]
                        by += [p[1] for p in ring] + [None]
                fig.add_scatter(x=bx, y=by, mode="lines", hoverinfo="skip", showlegend=False,
                                line={"width": 0.6, "color": "rgba(217,201,163,0.35)"})
            except Exception:
                pass
        fig.add_scatter(x=xs, y=ys, **trace)
        if tg:
            fig.add_scatter(x=[t["lon"] for t in tg], y=[t["lat"] for t in tg],
                            mode="markers+text", text=[t["name"] for t in tg],
                            textposition="top center", name="targets",
                            marker={"symbol": "x", "size": 11, "color": "#b08a5a"})
        xr, yr = [-180, 180], [-90, 90]
        if kind == "map_heat" and xs:
            pad = 4
            xr = [max(-180, min(xs) - pad), min(180, max(xs) + pad)]
            yr = [max(-90, min(ys) - pad), min(90, max(ys) + pad)]
        fig.update_xaxes(range=xr, dtick=30 if xr[1] - xr[0] > 90 else 10,
                         title="longitude (°) · " + body, gridcolor="rgba(42,42,36,.6)", zeroline=False)
        fig.update_yaxes(range=yr, dtick=30 if yr[1] - yr[0] > 60 else 10, title="latitude (°)",
                         gridcolor="rgba(42,42,36,.6)", zeroline=False, scaleanchor="x", scaleratio=1)
        tex = body_texture(body)
        if tex:
            fig.add_layout_image(source=tex, xref="x", yref="y", x=-180, y=90, sizex=360, sizey=180,
                                 sizing="stretch", layer="below", opacity=1.0)
        fig.add_annotation(text=body.upper(), xref="paper", yref="paper", x=0.99, y=0.98,
                           showarrow=False, font={"size": 11, "color": "#6e695e"})
    fig.update_layout(title=spec.get("title"), height=fig.layout.height or 520,
                      margin={"l": 30, "r": 20, "t": 50, "b": 30},
                      template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", colorway=PALETTE,
                      font={"family": "Inter, Segoe UI, sans-serif", "color": "#ece6d8"})
    if spec.get("x_title"):
        fig.update_layout(xaxis_title=spec["x_title"])
    if spec.get("y_title"):
        fig.update_layout(yaxis_title=spec["y_title"])
    fig.update_xaxes(gridcolor="#2a2a24", zerolinecolor="#2a2a24")
    fig.update_yaxes(gridcolor="#2a2a24", zerolinecolor="#2a2a24")
    return fig
