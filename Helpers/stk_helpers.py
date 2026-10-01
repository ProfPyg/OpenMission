"""
stk_helpers.py -- a small, version-independent wrapper around STK's COM API.

WHY THIS EXISTS
---------------
Scripts that talk to STK through ``win32com.client`` usually fail for one of
two reasons, neither of which has anything to do with the mission being
modelled:

1. ``win32com.client.constants`` is EMPTY when you attach to a running STK
   with ``GetActiveObject``. pywin32 only populates that module after makepy
   has generated wrappers for the exact type library on that machine. So
   ``constants.eSatellite`` / ``constants.eOrbitStateClassical`` raise a bare
   ``AttributeError`` naming the constant -- a confusing error that looks
   like a bug in the script.
2. ProgIDs are version-specific. ``STK12.Application`` does not exist on an
   STK 11 box and vice versa.

This module removes both failure modes:

* Structure, orbit state, geometry and constraints go through STK **Connect**
  commands -- plain text commands, identical across STK 11 / 12 / newer Ansys
  releases, requiring no enums at all.
* Object-model calls are used only where they take strings and numbers
  (``SetModel("Simple Transmitter Model")``, ``.Frequency``, ``.Eirp``,
  ``SetEmbeddedModel(...)``), which need no enums either.
* ProgIDs are tried in order until one resolves.

DESIGN RULES (keep these if you extend the module)
--------------------------------------------------
* Never import or reference ``win32com.client.constants``. If an enum
  value is genuinely needed, read it at runtime with ``session.enum()``,
  which walks the running STK's own type library.
* Never pass a numeric enum literal. If a call needs an enum, find the
  Connect equivalent instead.
* Every helper returns the object PATH it created (e.g.
  ``"*/Satellite/EOSat1"``). Paths are the currency of this module -- pass
  them to the next helper.
* Helpers are ADDITIVE. ``unique_path`` guarantees a new object is never
  created on top of an existing one.

QUICK START
-----------
    import stk_helpers as stk

    s = stk.StkSession.attach()
    s.ensure_scenario("MyScenario")
    s.set_time_period("1 Jul 2026 00:00:00.000", "2 Jul 2026 00:00:00.000")

    sat = s.new_satellite("Sat1", sma_km=6878.137, inc_deg=97.4, raan_deg=0.0)
    s.add_sensor_rectangular(sat, "Cam", 1.11, 0.83)
    s.point_nadir(sat + "/Sensor/Cam")
    s.add_transmitter(sat, "TX", freq_ghz=0.437, eirp_dbw=0.0)

    gs = s.new_facility("Svalbard", 78.229772, 15.407786, 0.458)
    s.add_receiver(gs, "RX", freq_ghz=0.437)
    s.set_min_elevation(gs, 5.0)

    for name, count, secs in s.access_summary([sat], gs):
        print(name, count, secs)

VERIFYING THIS MODULE
---------------------
Run ``verify_stk_helpers.py`` against a real STK install after ANY change
here. It exercises every helper in a throwaway scenario and prints PASS/FAIL
per feature, so a broken helper is caught before it reaches a user.
"""

from __future__ import annotations

import functools
import datetime as _dt
import inspect as _inspect
import os
import re as _re
import re
import sys
from datetime import datetime as _datetime

try:
    import win32com.client
    import pywintypes
    import pythoncom
except ImportError as _exc:  # pragma: no cover
    raise ImportError(
        "stk_helpers requires pywin32 (Windows only). Install it with:\n"
        "    pip install pywin32\n"
        "Original error: %s" % _exc
    )


__all__ = ["StkSession", "StkError", "DEFAULT_PROGIDS", "EARTH_RADIUS_KM",
           "MOON_RADIUS_KM", "GEO_SMA_KM", "altitude_to_sma",
           "walker_delta_elements", "gmst_deg", "parse_utcg",
           "precession_ra_deg", "julian_centuries"]


# ---------------------------------------------------------------------------
# Constants that are ours, not STK's
# ---------------------------------------------------------------------------

#: ProgIDs tried in order by :meth:`StkSession.attach`. The version-agnostic
#: "STK.Application" resolves to whatever STK is registered as default, so it
#: is the most portable; the pinned ones cover installs where the generic
#: entry is missing. Callers can override via the ``progids`` argument.
DEFAULT_PROGIDS = (
    "STK.Application",
    "STK_11",
    "STK12.Application",
    "STK11.Application",
    "STK11_x64.Application",
)

EARTH_RADIUS_KM = 6378.137
MOON_RADIUS_KM = 1737.4

#: Units this module pins on the STK root. Connect commands are interpreted
#: in the CURRENT unit preferences, so every session pins them explicitly --
#: otherwise a user whose STK is set to metres silently gets orbits 1000x
#: too small.
_UNIT_PREFERENCES = (
    ("DateFormat", "UTCG"),
    ("Distance", "km"),
    ("Angle", "deg"),
    ("Time", "sec"),
)


#: Type libraries loaded for their enums in addition to the root's own.
#: (GUID, major, minor) as registered by STK 11.
_EXTRA_TYPELIBS = (
    ("{090D317C-31A7-4AF7-89CD-25FE18F4017C}", 1, 0),     # AgStkGatorLib (Astrogator)
)

#: Semi-major axis of a geostationary orbit (radius, not altitude).
GEO_SMA_KM = 42164.137

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def parse_utcg(epoch):
    """Parse an STK UTCG string ("1 Jul 2026 00:00:00.000") to a datetime."""
    text = str(epoch).strip()
    match = _re.match(
        r"^(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})\s+"
        r"(\d{1,2}):(\d{2}):(\d{2}(?:\.\d+)?)$", text)
    if not match:
        raise ValueError("not a UTCG date/time: %r" % epoch)
    day, month_name, year, hour, minute, second = match.groups()
    try:
        month = _MONTHS.index(month_name[:3].title()) + 1
    except ValueError:
        raise ValueError("unknown month in %r" % epoch)
    whole = int(float(second))
    micro = int(round((float(second) - whole) * 1e6))
    return _datetime(int(year), month, int(day), int(hour), int(minute), whole, micro)


def julian_centuries(epoch):
    """Julian centuries from J2000.0 for a UTCG epoch."""
    when = epoch if isinstance(epoch, _datetime) else parse_utcg(epoch)
    year, month = when.year, when.month
    if month <= 2:
        year, month = year - 1, month + 12
    a = year // 100
    b = 2 - a + a // 4
    day_fraction = (when.hour + when.minute / 60.0
                    + (when.second + when.microsecond / 1e6) / 3600.0) / 24.0
    julian_day = (int(365.25 * (year + 4716)) + int(30.6001 * (month + 1))
                  + when.day + day_fraction + b - 1524.5)
    return (julian_day - 2451545.0) / 36525.0


def precession_ra_deg(epoch):
    """Accumulated general precession in right ascension since J2000, degrees.

    GMST is measured from the equinox OF DATE, but orbital elements here are
    given in J2000. The two frames drift apart by about 46.1 arcsec per year
    in right ascension, so by 2026 they differ by roughly a third of a
    degree. At geostationary radius a third of a degree is ~250 km of
    along-track position -- small enough to look like rounding, large enough
    to matter when parking a satellite in an assigned slot.
    """
    t = julian_centuries(epoch)
    return (4612.16 * t + 1.39656 * t * t) / 3600.0


def gmst_deg(epoch):
    """Greenwich Mean Sidereal Time in degrees for a UTCG epoch.

    Standard IAU 1982 series. UT1-UTC is ignored (< 0.9 s, i.e. < 0.004 deg
    of Earth rotation), which is far below what matters for placing a
    geostationary slot.
    """
    when = epoch if isinstance(epoch, _datetime) else parse_utcg(epoch)
    year, month = when.year, when.month
    if month <= 2:
        year, month = year - 1, month + 12
    a = year // 100
    b = 2 - a + a // 4
    day_fraction = (when.hour + when.minute / 60.0
                    + (when.second + when.microsecond / 1e6) / 3600.0) / 24.0
    julian_day = (int(365.25 * (year + 4716)) + int(30.6001 * (month + 1))
                  + when.day + day_fraction + b - 1524.5)
    d = julian_day - 2451545.0
    t = d / 36525.0
    gmst = (280.46061837 + 360.98564736629 * d
            + 0.000387933 * t * t - (t ** 3) / 38710000.0)
    return gmst % 360.0


class StkError(RuntimeError):
    """Raised for any STK interaction failure, with actionable context."""


def altitude_to_sma(altitude_km: float, body_radius_km: float = EARTH_RADIUS_KM) -> float:
    """Circular-orbit semi-major axis for a given altitude above a body."""
    return body_radius_km + float(altitude_km)


def walker_delta_elements(total_sats: int, num_planes: int, phase_f: int,
                          raan0_deg: float = 0.0, true_anom0_deg: float = 0.0):
    """Yield ``(index, plane, raan_deg, true_anomaly_deg)`` for a Walker Delta.

    Standard Walker ``T/P/F`` notation:

    * ``T`` = total satellites, ``P`` = planes, ``S = T/P`` sats per plane
    * RAAN spacing between planes        = 360 / P
    * in-plane spacing                   = 360 / S
    * phase step per plane increment     = F * 360 / T

    Example -- ``walker_delta_elements(4, 4, 1)`` gives four planes 90 deg
    apart in RAAN, each satellite offset 90 deg in true anomaly from the
    previous plane.
    """
    if total_sats <= 0 or num_planes <= 0:
        raise ValueError("total_sats and num_planes must be positive")
    if total_sats % num_planes != 0:
        raise ValueError(
            "Walker constellation requires total_sats (%d) to be divisible by "
            "num_planes (%d)" % (total_sats, num_planes))

    sats_per_plane = total_sats // num_planes
    raan_step = 360.0 / num_planes
    in_plane_step = 360.0 / sats_per_plane
    phase_step = float(phase_f) * 360.0 / total_sats

    for index in range(total_sats):
        plane = index // sats_per_plane
        slot = index % sats_per_plane
        raan = (raan0_deg + plane * raan_step) % 360.0
        true_anom = (true_anom0_deg + slot * in_plane_step + plane * phase_step) % 360.0
        yield index, plane, raan, true_anom


def _accept_aliases(**aliases):
    """Accept alternative spellings for keyword arguments.

    A generated script is written from a prompt, and a prompt can drift from
    the real signature. Where a shorter or longer spelling is an obvious
    synonym, accept it rather than failing with "unexpected keyword
    argument" -- the caller's intent is unambiguous. Check_prompt_api.py
    keeps the documentation honest; this keeps a stale caller working.

    ``_accept_aliases(short_name="canonical_name")``
    """
    def decorate(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for alias, canonical in aliases.items():
                if alias in kwargs and canonical not in kwargs:
                    kwargs[canonical] = kwargs.pop(alias)
            return func(*args, **kwargs)
        wrapper._aliases = aliases
        return wrapper
    return decorate


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
class DataResult(object):
    """What an STK data provider returned.

    ``kind``       "time-varying", "interval" or "fixed"
    ``message``    STK's own explanation -- "OK", or e.g. "No eclipses for
                   this time period." An empty result is an ANSWER, not an
                   error; always report the message alongside it.
    ``columns``    {element name: [values]} -- one ROW per sample, grid point
                   or event
    ``intervals``  for interval providers, [(start, stop), ...] -- one per
                   event, taken from the "Start Time"/"Stop Time" columns

    MEASURED (bridge jobs 0043, 0058): how rows are spread over STK's
    result.Intervals DEPENDS ON THE PROVIDER. Lighting packs every eclipse
    into one interval; access data puts each PASS in its own interval, and
    the top-level DataSets then hold only the FIRST pass -- reading them
    alone returned 37 rows of a six-pass day and a 2,264 km "peak". Rows are
    therefore always gathered across every interval. For lighting, too,
    result.Intervals is the requested window, not the events. An equatorial satellite's umbra comes
    back as ONE result interval holding FOUR rows -- four 34-minute eclipses.
    Reading result.Intervals as the events reported one eclipse spanning six
    hours: plausible, and wrong.
    """

    def __init__(self, path, provider, group, kind, message, columns, intervals,
                 segments=None):
        self.path, self.provider, self.group = path, provider, group
        self.kind, self.message = kind, message
        self.columns, self.intervals = columns, intervals
        #: [(start, stop, first_row, n_rows)] -- one per result interval.
        #: For access data each segment is ONE PASS.
        self.segments = segments or []

    def __len__(self):
        return max((len(v) for v in self.columns.values()), default=0)

    @property
    def empty(self):
        return len(self) == 0

    def column(self, name):
        """One column, matched case-insensitively; raises listing the others."""
        for key, values in self.columns.items():
            if key.lower() == str(name).lower():
                return values
        raise StkError("'%s' has no element '%s'. Elements: %s."
                       % (self.provider, name, ", ".join(self.columns)))

    def rows(self):
        """Rows as dicts, in the order STK returned them."""
        names = list(self.columns)
        for index in range(len(self)):
            yield {n: (self.columns[n][index] if index < len(self.columns[n]) else None)
                   for n in names}

    def __repr__(self):
        return ("<DataResult %s / %s%s: %s, %d row(s), %s>"
                % (self.path, self.provider,
                   (" [%s]" % self.group) if self.group else "",
                   self.kind, len(self), self.message))


class _PathString(str):
    """A path that is a plain string but also accepts pathlib's ``/``.

    Generated scripts kept writing ``session.output_dir() / "results.csv"``,
    which is the pathlib idiom and died with "unsupported operand type(s) for
    /: 'str' and 'str'" AFTER the whole scenario had been built. Since the
    intent is unambiguous, join instead of raising.

    Subclassing str rather than returning a Path keeps every existing use
    working -- os.path.join, %-formatting, .startswith, string concatenation
    -- none of which a pathlib.Path supports the same way.
    """

    def __truediv__(self, other):
        return _PathString(os.path.join(self, str(other)))

    def __rtruediv__(self, other):
        return _PathString(os.path.join(str(other), self))


class StkSession(object):
    """A connected STK session. Construct with :meth:`attach` or :meth:`launch`."""

    def __init__(self, app):
        self.app = app
        try:
            self.root = app.Personality2  # object-model root, all STK versions
        except Exception as exc:
            raise StkError("Connected to STK but could not obtain the "
                           "Personality2 root object: %s" % exc)
        self.progid = None
        #: (path, constraint) pairs this STK would not accept. Anything here
        #: means the corresponding results are OPTIMISTIC.
        self.unapplied_constraints = []
        self.set_units()

    # -- connection ---------------------------------------------------------
    @classmethod
    def attach(cls, progids=None):
        """Attach to an ALREADY-RUNNING STK instance.

        Preferred over :meth:`launch`: launching creates a second, invisible
        STK the user cannot see. Raises :class:`StkError` listing every
        ProgID tried if none resolve.
        """
        progids = tuple(progids) if progids else DEFAULT_PROGIDS
        attempts = []
        for progid in progids:
            try:
                app = win32com.client.GetActiveObject(progid)
            except Exception as exc:
                attempts.append("    %-24s -> %s" % (progid, exc))
                continue
            session = cls(app)
            session.progid = progid
            return session
        raise StkError(
            "Could not attach to a running STK instance.\n"
            "  ProgIDs attempted:\n%s\n"
            "  Fix: open STK and re-run, or pass progids=[...] with the ProgID\n"
            "  registered on this machine. Find it in PowerShell:\n"
            "    Get-ChildItem Registry::HKEY_CLASSES_ROOT | "
            "Where-Object { $_.PSChildName -like \"*STK*Application*\" } | "
            "Select-Object PSChildName" % "\n".join(attempts))

    @classmethod
    def launch(cls, progids=None, visible=True):
        """Start a NEW STK instance. Only use when explicitly asked to."""
        progids = tuple(progids) if progids else DEFAULT_PROGIDS
        attempts = []
        for progid in progids:
            try:
                app = win32com.client.Dispatch(progid)
                app.Visible = bool(visible)
                app.UserControl = True
            except Exception as exc:
                attempts.append("    %-24s -> %s" % (progid, exc))
                continue
            session = cls(app)
            session.progid = progid
            return session
        raise StkError("Could not launch STK.\n  ProgIDs attempted:\n%s"
                       % "\n".join(attempts))

    # -- low-level ----------------------------------------------------------
    # -- enums, read from the running STK's own type library ----------------
    def _load_enums(self):
        """Build ``{enum_name: value}`` by reading STK's type library.

        This is the reliable replacement for ``win32com.client.constants``.
        That module is only populated by makepy; here we walk the type
        library the running STK application already exposes through
        ``GetTypeInfo``, so the values come from THIS machine's STK, whatever
        version it is. Nothing is hardcoded and no code generation is needed.
        """
        enums = {}
        # STKUtil (reached through ConversionUtility) holds shared enums such
        # as AgECoordinateSystem (J2000, ICRF, TEME, Fixed...) that STKObjects
        # does not -- orbit-state input needs them.
        sources = [self.root, self.app]
        try:
            sources.append(self.root.ConversionUtility)
        except Exception:
            pass
        # Libraries whose enums are not reachable from the root object at all.
        # MEASURED (bridge job 0101): without Astrogator's, find_enum(
        # "eVASegmentTypeManeuver") returned None, STK took None as 0, and four
        # "manoeuvres" were silently inserted as Initial State segments.
        for guid, major, minor in _EXTRA_TYPELIBS:
            try:
                lib = pythoncom.LoadRegTypeLib(guid, major, minor, 0)
                for i in range(lib.GetTypeInfoCount()):
                    if lib.GetTypeInfoType(i) != pythoncom.TKIND_ENUM:
                        continue
                    info = lib.GetTypeInfo(i)
                    for v in range(info.GetTypeAttr().cVars):
                        var = info.GetVarDesc(v)
                        enums[info.GetNames(var.memid)[0]] = var.value
            except Exception:
                pass
        for source in sources:
            try:
                type_info = source._oleobj_.GetTypeInfo()
                remote_lib, _index = type_info.GetContainingTypeLib()
            except Exception:
                continue
            # MEASURED (bridge jobs 0002/0005): walking the library through
            # the live STK connection makes every lookup a cross-process call
            # -- the full dump timed out at 180 s. Loading the SAME library
            # in-process from the registry took 1.4 s. Ask STK which library,
            # then read it locally; fall back to the remote one if needed.
            typelib = remote_lib
            try:
                attr = remote_lib.GetLibAttr()
                typelib = pythoncom.LoadRegTypeLib(attr[0], attr[3], attr[4], attr[1])
            except Exception:
                pass
            for i in range(typelib.GetTypeInfoCount()):
                try:
                    if typelib.GetTypeInfoType(i) != pythoncom.TKIND_ENUM:
                        continue
                    enum_info = typelib.GetTypeInfo(i)
                    attributes = enum_info.GetTypeAttr()
                    for v in range(attributes.cVars):
                        var = enum_info.GetVarDesc(v)
                        name = enum_info.GetNames(var.memid)[0]
                        enums[name] = var.value
                except Exception:
                    continue
        return enums

    def enum(self, name):
        """Return an STK enum value by name, e.g. ``enum("eSatellite")``.

        Raises :class:`StkError` naming the enum if this STK's type library
        does not define it -- far clearer than the bare ``AttributeError``
        that ``win32com.client.constants`` produces.
        """
        if getattr(self, "_enums", None) is None:
            self._enums = self._load_enums()
        try:
            return self._enums[name]
        except KeyError:
            raise StkError(
                "Enum '%s' is not defined in this STK installation's type "
                "library (%d enum values were read). Either the name is wrong "
                "or this STK version calls it something else." % (name, len(self._enums)))

    def find_enum(self, preferred, *fragments):
        """Return an enum VALUE by preferred name, else by name fragments.

        STK enum names are not identical across versions, so a helper that
        needs one should describe it rather than hardcode a single spelling.
        Returns None when nothing matches, so callers can fall back.
        """
        if getattr(self, "_enums", None) is None:
            self._enums = self._load_enums()
        if preferred in self._enums:
            return self._enums[preferred]
        for name in sorted(self._enums):
            low = name.lower()
            if all(f.lower() in low for f in fragments):
                return self._enums[name]
        return None

    def has_enums(self):
        """True if enum values could be read from the type library."""
        if getattr(self, "_enums", None) is None:
            self._enums = self._load_enums()
        return bool(self._enums)

    @staticmethod
    def _com_reason(exc):
        """Pull the human-readable reason out of a pywin32 com_error."""
        try:
            excepinfo = exc.excepinfo
            if excepinfo and len(excepinfo) > 2 and excepinfo[2]:
                return str(excepinfo[2])
        except Exception:
            pass
        return str(exc)

    #: Object classes the module wraps. A hand-written "New /" for any of
    #: these is refused, because three separate generated scripts invented
    #: three different (all wrong) ways to create a facility on the Moon --
    #: New / */CentralBody/Moon Facility X, New / */Planet/Moon Facility X,
    #: and others -- while new_facility(..., central_body="Moon") sat in the
    #: API listing they were given. Prose did not stop it; a guard does.
    _WRAPPED_CLASSES = {
        "facility": 'new_facility(name, lat, lon, central_body="Moon")',
        "place": 'new_facility(name, lat, lon, central_body="Moon")',
        "target": 'new_facility(name, lat, lon, central_body="Moon")',
        "satellite": 'new_satellite(name, sma_km, inc_deg, raan_deg, central_body="Moon")',
        "areatarget": 'new_area_target(name, boundary, central_body="Moon")',
        "chain": "new_chain(name, [member, ...])",
        "coveragedefinition": "new_coverage(name, lat_min, lat_max, lon_min, lon_max)",
        "figureofmerit": "add_figure_of_merit(coverage_path, name)",
        "sensor": "add_sensor_conic(parent, name, half_angle_deg)",
        "transmitter": "add_transmitter(parent, name, freq_ghz, eirp_dbw)",
        "receiver": "add_receiver(parent, name)",
        "planet": "new_planet(name)",
    }

    def _normalise_paths(self, command):
        """Strip invented body prefixes out of object paths in any command.

        Rewriting the CREATION command was not enough: a script that invents
        "New / */CentralBody/Moon Facility Tranquility" goes on to use
        "*/CentralBody/Moon/Facility/Tranquility" as the object's path in
        every later command. STK's real path is "*/Facility/Tranquility" --
        the central body is a PROPERTY of the object, never a path segment.

        Only paths with something AFTER the body are touched, so a genuine
        planet object path ("*/Planet/Moon") is left alone.

        Returns the corrected command, or None if nothing needed changing.
        """
        text = str(command)
        tokens = text.split()
        changed = False
        for index, token in enumerate(tokens):
            # Paths are often quoted ('View To "*/Facility/X"'), so the
            # leading quote has to come off before the "*/" test.
            lead = token[:len(token) - len(token.lstrip('"\''))]
            core = token[len(lead):]
            if not core.startswith("*/"):
                continue
            stripped = core.rstrip('"\',')
            trailer = core[len(stripped):]
            parts = [p for p in stripped.split("/") if p and p != "*"]
            if len(parts) > 2 and parts[0].lower() in ("centralbody", "planet"):
                tokens[index] = lead + "*/" + "/".join(parts[2:]) + trailer
                changed = True
        if not changed:
            return None
        corrected = " ".join(tokens)
        sys.stderr.write(
            "[fixed] rewrote an invented object path\n"
            "        from: %s\n"
            "          to: %s\n"
            "        A central body is a property of an object, not a path\n"
            "        segment. Use the path the helper returned.\n"
            % (text, corrected))
        return corrected

    def _rewrite_creation(self, command):
        """Turn a hand-written creation command into one STK accepts.

        Generated scripts keep inventing ways to hang a facility off a body:
        "New / */CentralBody/Moon Facility X", "New / */Planet/Moon Facility
        X". Neither exists. Four rounds of clearer instructions did not stop
        it, so rather than abort the whole run over syntax, the command is
        rewritten into the form STK does accept:

            New / */Facility X CentralBody Moon

        Returns the corrected command and the body, or (None, None) if the
        command cannot be understood -- in which case the caller raises.
        """
        text = " ".join(str(command).split())
        if not text.lower().startswith("new "):
            return None, None

        tokens = text.split()
        body = None
        class_name = None
        object_name = None

        for token in tokens[1:]:
            cleaned = token.strip("*/")
            if not cleaned:
                continue
            parts = [p for p in token.split("/") if p and p != "*"]
            # */CentralBody/Moon or */Planet/Moon -> the body is the last part
            if len(parts) >= 2 and parts[0].lower() in ("centralbody", "planet"):
                body = parts[-1]
                continue
            if len(parts) == 1 and parts[0].lower() in self._WRAPPED_CLASSES:
                class_name = parts[0]
                continue
            if class_name and object_name is None and cleaned.lower() not in ("centralbody",):
                object_name = cleaned
        # "CentralBody Moon" given as a trailing keyword pair
        for index, token in enumerate(tokens[:-1]):
            if token.lower() == "centralbody":
                body = tokens[index + 1]

        if not class_name or not object_name:
            return None, None
        corrected = "New / */%s %s" % (class_name, object_name)
        if body:
            corrected += " CentralBody %s" % body
        return corrected, body

    def _guard_creation(self, command):
        """Rewrite a hand-written creation command, or refuse it.

        Being strict here aborted whole runs over a syntax detail the script
        got wrong but whose INTENT was unambiguous. Being liberal in what is
        accepted -- while saying loudly what was changed -- delivers the
        scenario and still tells the author to use the helper.
        """
        text = str(command).strip()
        if not text.lower().startswith("new "):
            return None
        lowered = text.lower().replace("/", " ").replace("*", " ")
        if not any(name in lowered.split() for name in self._WRAPPED_CLASSES):
            return None

        corrected, body = self._rewrite_creation(text)
        if corrected:
            sys.stderr.write(
                "[fixed] rewrote a hand-written creation command\n"
                "        from: %s\n"
                "          to: %s\n"
                "        STK has no 'New / */CentralBody/<body> ...' or\n"
                "        'New / */Planet/<body> ...' form. Prefer the helper --\n"
                "        it also sets the position and verifies the body.\n"
                % (text, corrected))
            return corrected

        for class_name, replacement in self._WRAPPED_CLASSES.items():
            if class_name in lowered.split():
                raise StkError(
                    "Cannot run this creation command and cannot repair it:\n"
                    "    %s\n"
                    "  Use session.%s instead." % (text, replacement))
        return None

    def cmd(self, command, label="", fatal=True, allow_create=False):
        """Execute one Connect command.

        Creation commands for object types the module already wraps are
        REFUSED, because every generated script that hand-wrote one got the
        syntax wrong. Use the helper named in the error instead. Pass
        ``allow_create=True`` only when deliberately exploring a form STK may
        not support -- probe scripts do this.

        Connect is STK's text command interface -- the same commands you can
        type into STK's own command window. Text commands are stable across
        STK versions, which is why this module prefers them to enum-taking
        object-model calls.

        Returns True on success. With ``fatal=False`` a failure prints a
        warning and returns False instead of raising.

        The error message keeps the reason on the FIRST line, so a caller
        that only shows one line of an exception still shows something
        useful.
        """
        # Only guard calls from generated scripts; the module's own helpers
        # legitimately issue "New /" commands.
        try:
            caller = _inspect.currentframe().f_back.f_globals.get("__name__", "")
        except Exception:
            caller = ""
        # Unconditional: a bad path usually arrives as an ARGUMENT to a
        # helper, so by the time the command is built the caller is this
        # module. The module's own paths are already correct, so this is a
        # no-op for them.
        corrected = self._normalise_paths(command)
        if corrected:
            command = corrected
        if caller != __name__ and not allow_create:
            corrected = self._guard_creation(command)
            if corrected:
                command = corrected

        try:
            self.root.ExecuteCommand(command)
            return True
        except pywintypes.com_error as exc:
            message = ("STK rejected a Connect command%s: %s\n    command: %s"
                       % ((" [%s]" % label) if label else "",
                          self._com_reason(exc), command))
            if fatal:
                raise StkError(message)
            sys.stderr.write("[warn] %s\n" % message)
            return False

    def clean_path(self, path):
        """Repair an object path before it reaches the object model.

        cmd() normalises paths inside Connect command TEXT, but object-model
        routes (chain members, obj()) take a bare path and never pass through
        it. Both funnel through here so an invented "*/CentralBody/Moon/..."
        path is repaired wherever it enters the module.
        """
        corrected = self._normalise_paths(path)
        return corrected if corrected else path

    def obj(self, path):
        """Return the object-model interface for an STK path, or raise."""
        path = self.clean_path(path)
        try:
            return self.root.GetObjectFromPath(path)
        except Exception as exc:
            raise StkError("No STK object at path '%s': %s" % (path, exc))

    def exists(self, path):
        """True if an object exists at ``path``."""
        try:
            self.root.GetObjectFromPath(path)
            return True
        except Exception:
            return False

    @staticmethod
    def _require_path(path, who):
        """Guard against a None path silently producing 'New / None/Sensor'.

        A helper whose parent failed returns None; without this the next
        helper builds a nonsense command and reports a confusing second
        error that hides the real one.
        """
        if not path or not isinstance(path, str):
            raise StkError(
                "%s was given an invalid parent path (%r). This usually means "
                "the helper that should have created the parent object failed "
                "earlier -- fix that first error, not this one." % (who, path))
        return path

    def unique_path(self, base_path):
        """Return ``base_path``, or ``base_path_2``, ``_3``... if taken.

        This is what makes every helper additive: running a script twice adds
        a second set of objects instead of failing or overwriting the first.
        """
        path, suffix = base_path, 1
        while self.exists(path):
            suffix += 1
            path = "%s_%d" % (base_path, suffix)
        return path

    def output_dir(self, out_dir=None):
        """The folder files are written to, created if needed.

        Beside the RUNNING SCRIPT, not beside stk_helpers.py -- so a script
        run from an unzipped bundle writes into that bundle. Printed once per
        session by :meth:`announce_output_dir` so nobody has to go looking.
        """
        resolved = out_dir or getattr(self, "_default_out", None) or os.path.join(
            os.path.dirname(os.path.abspath(sys.argv[0] or __file__)), "stk_output")
        os.makedirs(resolved, exist_ok=True)
        return _PathString(resolved)

    def announce_output_dir(self):
        """Print where output files will be written."""
        print("[output] files will be written to: %s" % self.output_dir())
        return self.output_dir()

    # -- scenario -----------------------------------------------------------
    def set_units(self):
        """Pin the unit preferences Connect commands are interpreted in."""
        for dimension, unit in _UNIT_PREFERENCES:
            try:
                self.root.UnitPreferences.Item(dimension).SetCurrentUnit(unit)
            except Exception as exc:
                sys.stderr.write("[warn] could not set unit preference %s=%s: %s\n"
                                 % (dimension, unit, exc))

    @property
    def scenario_name(self):
        """Name of the open scenario, or None if no scenario is open."""
        try:
            return self.root.CurrentScenario.InstanceName
        except Exception:
            return None

    def ensure_scenario(self, name, reuse_open=True):
        """Return the name of a usable scenario, creating one if needed.

        With ``reuse_open=True`` (default) an already-open scenario is used
        whatever its name -- helpers name objects uniquely, so adding to a
        user's open scenario is safe. With ``reuse_open=False`` an open
        scenario whose name differs raises, which is the right choice for a
        script that must not touch the wrong scenario.
        """
        open_name = self.scenario_name
        if open_name is not None:
            if reuse_open or open_name == name:
                self.set_units()
                return open_name
            raise StkError(
                "Scenario '%s' is open but this script targets '%s'. Close it, "
                "or call ensure_scenario(..., reuse_open=True)." % (open_name, name))
        try:
            self.root.NewScenario(str(name))
        except pywintypes.com_error as exc:
            raise StkError("Failed to create scenario '%s': %s" % (name, exc))
        self.set_units()
        return self.scenario_name

    def set_time_period(self, start, stop, rewind=True):
        """Set the scenario analysis period. Times are UTCG strings."""
        self.cmd('SetAnalysisTimePeriod * "%s" "%s"' % (start, stop),
                 label="scenario analysis period")
        if rewind:
            self.rewind()
        self._start, self._stop = start, stop

    def rewind(self):
        try:
            self.root.Rewind()
        except Exception:
            pass

    def save_scenario(self):
        self.cmd("Save / *", label="save scenario", fatal=False)

    def close_scenario(self):
        """Close the open scenario WITHOUT saving. Used by the smoke test."""
        self.cmd("Unload / *", label="close scenario", fatal=False)

    # -- central bodies -----------------------------------------------------
    #
    # STK 11 knows 31 bodies (probe_central_bodies.py): Ariel, Callisto,
    # Ceres, Charon, Deimos, Dione, Earth, Enceladus, Europa, Ganymede,
    # Hyperion, Iapetus, Io, Jupiter, Mars, Mercury, Mimas, Moon, Neptune,
    # Phobos, Phoebe, Pluto, Rhea, Saturn, Sun, Tethys, Titan, Titania,
    # Triton, Uranus, Venus. Facilities and satellites were verified on Mars
    # as well as the Moon, so nothing here is Moon-specific.
    #
    # Only Earth, Moon and Sun are exposed as NAMED properties; everything
    # else has to be reached through Item(), which is why looking up
    # CentralBodies.Mars fails.

    def _central_body(self, name):
        """The STK central-body object for a name, or None."""
        try:
            bodies = self.root.CentralBodies
        except Exception:
            return None
        try:
            return bodies.Item(str(name))
        except Exception:
            pass
        try:
            for index in range(int(bodies.Count)):
                item = bodies.Item(index)
                if str(getattr(item, "Name", "")).lower() == str(name).lower():
                    return item
        except Exception:
            pass
        return None

    def body_radius_km(self, name):
        """Equatorial radius of a central body, asked of STK.

        Hardcoding radii means a script can only reach the bodies somebody
        remembered to add a constant for -- and a Mars orbiter then depends
        on the agent recalling 3389.5 correctly. Reading it from STK makes
        every body STK knows usable.
        """
        body = self._central_body(name)
        if body is None:
            raise StkError(
                "STK does not know a central body called '%s'. Known bodies: %s."
                % (name, ", ".join(self.central_body_names()) or "unavailable"))
        # MEASURED on STK 11 by probe_body_attrs.py: the ellipsoid exposes
        # A, B, C (semi-axes, km) plus MeanRadius and VolumetricRadius.
        # A is the equatorial radius -- Earth 6378.137, Moon 1737.4,
        # Mars 3396.19. The SemiMajorAxis/EquatorialRadius/Radius names
        # tried first here exist on no build we have seen; they are kept
        # only so a different STK version can still satisfy the call.
        for path in (("Ellipsoid", "A"), ("Ellipsoid", "MeanRadius"),
                     ("Ellipsoid", "SemiMajorAxis"), ("Ellipsoid", "EquatorialRadius"),
                     ("Ellipsoid", "Radius"), ("Radius",), ("EquatorialRadius",)):
            current = body
            try:
                for attribute in path:
                    current = getattr(current, attribute)
                value = float(current)
                # STK reports these in metres on some builds
                return value / 1000.0 if value > 1e5 else value
            except Exception:
                continue
        raise StkError("Could not read a radius for '%s' from STK." % name)

    def body_mu(self, name):
        """Gravitational parameter (km^3/s^2) of a central body, from STK."""
        body = self._central_body(name)
        if body is None:
            raise StkError("STK does not know a central body called '%s'." % name)
        try:
            value = float(body.GravitationalParameter)
        except Exception as exc:
            raise StkError("Could not read mu for '%s': %s" % (name, exc))
        return value / 1e9 if value > 1e12 else value

    def central_body_names(self):
        """Every central body this STK knows, sorted."""
        names = []
        try:
            bodies = self.root.CentralBodies
            for index in range(int(bodies.Count)):
                try:
                    names.append(str(bodies.Item(index).Name))
                except Exception:
                    continue
        except Exception:
            return []
        return sorted(names)

    def sma_for_altitude(self, altitude_km, central_body="Earth"):
        """Semi-major axis for a circular orbit at an altitude above ANY body.

        Use this instead of stk.altitude_to_sma when the body is not Earth or
        the Moon: the radius comes from STK, so Mars, Titan, Europa and the
        other 27 bodies work without anyone hardcoding a number.
        """
        return self.body_radius_km(central_body) + float(altitude_km)

    # -- graphics windows ---------------------------------------------------
    #
    # STK's 3D and 2D windows open centred on Earth whatever the scenario
    # contains, so a lunar mission is drawn as a set of objects around the
    # wrong body -- correct data, unreadable picture. The Connect syntax for
    # re-centring is not something to guess at, so several documented-looking
    # forms are tried and the winner cached.

    #: 'VO * CentralBody <body>' was the form STK 11 accepted (probe_views.py);
    #: the rest stay as fallbacks for other builds.
    _VIEW_FORMS_3D = (
        'VO * CentralBody {body}',
        'VO */ CentralBody {body}',
        'VO * SetCentralBody {body}',
        'Window3D * CentralBody {body}',
    )
    #: No 2D form was accepted on STK 11 with '*'; the '*/' variants are
    #: tried too since that spelling worked for VO.
    _VIEW_FORMS_2D = (
        'Map */ CentralBody {body}',
        'Map * CentralBody {body}',
        'Map */1 CentralBody {body}',
        'Window2D * CentralBody {body}',
    )
    _VIEW_NEW_3D = (
        'New / */Window3D {body}',
        'Window3D * New CentralBody {body}',
        'VO * Window New CentralBody {body}',
    )

    def set_view_central_body(self, body, three_d=True, two_d=True):
        """Re-centre the 3D view on a central body. The 2D map does not move.

        MEASURED on STK 11 (probe_views.py, probe_views2.py):

        * 3D  -- ``VO * CentralBody <body>`` works, confirmed by eye.
        * 2D  -- sixteen Connect spellings were rejected and no object-model
                 route was found. The 2D map appears to follow the SCENARIO's
                 central body, which is fixed when the scenario is created,
                 so a lunar scenario built in an Earth scenario keeps an
                 Earth map. Untested hypothesis, recorded in CAPABILITIES.md.

        Returns ``{"3D": bool, "2D": bool}``. Failure is reported, not
        raised: a scenario with the right objects and an awkward camera is
        still correct, just harder to read.
        """
        result = {"3D": False, "2D": False}
        body = str(body)

        if three_d:
            cached = getattr(self, "_view_form_3d", None)
            forms = (cached,) if cached else self._VIEW_FORMS_3D
            for template in forms:
                if self.cmd(template.format(body=body), fatal=False):
                    self._view_form_3d = template
                    result["3D"] = True
                    break

        if two_d:
            cached = getattr(self, "_view_form_2d", None)
            forms = (cached,) if cached else self._VIEW_FORMS_2D
            for template in forms:
                if self.cmd(template.format(body=body), fatal=False):
                    self._view_form_2d = template
                    result["2D"] = True
                    break

        if result["3D"] and not result["2D"] and two_d:
            print("[view] 3D re-centred on %s; the 2D map stays on the "
                  "scenario's body (a known STK 11 limitation)." % body)
        elif not (result["3D"] or result["2D"]):
            sys.stderr.write(
                "[warn] could not re-centre any view on %s -- the scenario is "
                "correct but the windows show the default body. Change it by "
                "hand in STK.\n" % body)
        else:
            print("[view] re-centred on %s (3D: %s, 2D: %s)"
                  % (body, result["3D"], result["2D"]))
        return result

    def central_bodies_in_use(self):
        """Central bodies of the objects in the scenario, most common first.

        The SCENARIO object is excluded. It reports Earth whatever it
        contains, so counting it made a scenario holding a single lunar
        orbiter come back as [("Earth", 1), ("Moon", 1)] -- a tie that
        focus_on_scenario_body() then resolved towards Earth, which is
        exactly backwards for a lunar mission.
        """
        counts = {}
        try:
            names = str(self.root.ExecuteCommand("AllInstanceNames /").Item(0)).split()
        except Exception:
            return []
        for path in names:
            parts = [p for p in path.split("/") if p and p != "*"]
            if len(parts) < 2 or parts[-2].lower() == "scenario":
                continue
            body = None
            for getter in ("CentralBodyName", "CentralBody"):
                try:
                    body = str(getattr(self.obj(path), getter))
                    break
                except Exception:
                    continue
            if body:
                counts[body] = counts.get(body, 0) + 1
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    def focus_on_scenario_body(self, prefer_non_earth=True):
        """Re-centre the views on whichever body the scenario is really about.

        With ``prefer_non_earth`` (the default), any non-Earth body wins over
        Earth even if Earth has more objects: a lunar mission usually keeps
        Earth in the scenario as a reference, and centring on Earth would
        show the mission as a dot beside it.
        """
        bodies = self.central_bodies_in_use()
        if not bodies:
            return None
        body = bodies[0][0]
        if prefer_non_earth:
            others = [name for name, _count in bodies if name.lower() != "earth"]
            if others:
                body = others[0]
        self.set_view_central_body(body)
        return body

    # -- satellites ---------------------------------------------------------
    def new_satellite(self, name, sma_km, inc_deg, raan_deg,
                      ecc=0.0, arg_perigee_deg=0.0, true_anomaly_deg=0.0,
                      propagator="J2Perturbation", start=None, stop=None,
                      step_sec=60.0, coord_system=None, epoch=None,
                      central_body=None):
        """Create a satellite and set its classical orbit in one step.

        The Connect ``SetState Classical`` command sets the propagator, the
        elements AND propagates the ephemeris, so no separate Propagate()
        call is needed -- downstream access/AER/export work immediately.

        ``propagator`` is a Connect propagator name: "J2Perturbation" (fast,
        good for reference/GNSS constellations, needs no gravity file),
        "TwoBody", "HPOP" (high precision -- see :meth:`set_hpop_gravity`),
        "J4Perturbation".

        ``central_body`` selects the body the satellite orbits. Each
        satellite carries its own central body in STK, so one scenario can
        mix Earth and Moon orbiters. Leave as None for the scenario's body.

        ``sma_km`` is the SEMI-MAJOR AXIS, not altitude -- use
        :func:`altitude_to_sma` to convert.

        Returns the satellite path, e.g. ``"*/Satellite/Sat1"``.
        """
        start = start or getattr(self, "_start", None)
        stop = stop or getattr(self, "_stop", None)
        if not start or not stop:
            raise StkError("new_satellite needs a time period. Call "
                           "set_time_period() first, or pass start=/stop=.")
        epoch = epoch or start

        path = self.unique_path("*/Satellite/%s" % name)
        actual = path.rsplit("/", 1)[-1]

        # ALWAYS name the body: Connect reuses the last one used when it is
        # omitted, which is how an Earth ground station ended up on the Moon.
        body = central_body or "Earth"
        # MEASURED (benchmark lunar_shackleton): with J2000 an "86 deg" lunar
        # orbit is inclined to EARTH's equator -- its track reached only 62 S.
        # Away from Earth the elements must be in the body's own inertial frame.
        if coord_system is None:
            coord_system = "J2000" if body.lower() == "earth" else "Inertial"
        created = self.cmd("New / */Satellite %s CentralBody %s" % (actual, body),
                           label="create satellite on %s" % body, fatal=False)
        if not created:
            self.cmd("New / */Satellite %s" % actual, label="create satellite")
            sys.stderr.write(
                "[warn] central body '%s' not applied to %s; it will use "
                "whatever body STK defaults to.\n" % (body, actual))

        self._set_classical_state(
            path, propagator=propagator, start=start, stop=stop,
            step_sec=step_sec, coord_system=coord_system, epoch=epoch,
            sma_km=sma_km, ecc=ecc, inc_deg=inc_deg,
            arg_perigee_deg=arg_perigee_deg, raan_deg=raan_deg,
            true_anomaly_deg=true_anomaly_deg)
        self._assert_central_body(path, body)
        return path

    #: Single-satellite argument names that a caller may reasonably pass to
    #: new_walker by analogy with new_satellite. Walker derives these per
    #: satellite, so they are re-interpreted as the constellation's STARTING
    #: offsets rather than rejected -- passing them through would collide
    #: ("got multiple values for keyword argument 'raan_deg'").
    _WALKER_ALIASES = {
        "raan_deg": "raan0_deg",
        "true_anomaly_deg": "true_anom0_deg",
    }

    #: SetState TLE argument forms, probed in order and cached like the
    #: classical ones. STK 11 rejected the TimePeriod form for classical
    #: elements, so do not assume it works here either.
    _TLE_FORMS = (
        ("UseScenarioInterval",
         'SetState {path} TLE "{line1}" "{line2}" UseScenarioInterval {step:g}'),
        ("TimePeriod",
         'SetState {path} TLE "{line1}" "{line2}" TimePeriod "{start}" "{stop}" {step:g}'),
        ("bare",
         'SetState {path} TLE "{line1}" "{line2}"'),
    )

    def new_satellite_from_tle(self, name, line1, line2, step_sec=60.0,
                               start=None, stop=None):
        """Create a satellite propagated by SGP4 from a two-line element set.

        SGP4 is the model a TLE is defined against -- feeding TLE-derived
        elements to a Keplerian propagator is a different orbit -- so this
        always uses SGP4.

        Two routes are tried, because Connect's SetState TLE forms were
        rejected on STK 11 (and the one that was accepted produced no
        ephemeris):

          1. object model: set the SGP4 propagator, load the TLE from a
             temporary file via the propagator's CommonTasks, set the
             ephemeris interval to the scenario, propagate.
          2. Connect SetState TLE forms, then an explicit Propagate.

        Both are verified the same way: there must be ephemeris at BOTH ends
        of the scenario window. A TLE that propagates only ~1.5 h from its
        epoch is a known trap, and shows up later as "no passes" rather than
        as an error.
        """
        line1, line2 = str(line1).strip(), str(line2).strip()
        for index, line in ((1, line1), (2, line2)):
            if not line.startswith("%d " % index) or len(line) < 60:
                raise StkError(
                    "TLE line %d does not look like a TLE (got %r). Expected a "
                    "69-character line starting with '%d '." % (index, line[:30], index))
        ssc = line1[2:7].strip()
        self._check_tle_plausible(line1, line2, ssc, start)

        start = start or getattr(self, "_start", None)
        stop = stop or getattr(self, "_stop", None)
        path = self.unique_path("*/Satellite/%s" % name)
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / */Satellite %s" % actual, label="create satellite")

        attempts = []

        # ---- route 1: object model with a temporary TLE file -------------
        tle_file = os.path.join(self.output_dir(), "_tle_%s.tle" % ssc)
        try:
            with open(tle_file, "w") as handle:
                handle.write("%s\n%s\n" % (line1, line2))
        except OSError as exc:
            attempts.append("    [object model] could not write a TLE file: %s" % exc)
            tle_file = None

        if tle_file:
            try:
                satellite = self.obj(path)
                sgp4 = self.find_enum("ePropagatorSGP4", "propagator", "sgp4")
                if sgp4 is None:
                    raise StkError("no SGP4 propagator enum in this type library")
                satellite.SetPropagatorType(sgp4)
                propagator = satellite.Propagator
                loaded = False
                for method, args in (("AddSegsFromFile", (ssc, tle_file)),
                                     ("AddSegsFromFile", (str(int(ssc)), tle_file))):
                    try:
                        getattr(propagator.CommonTasks, method)(*args)
                        loaded = True
                        break
                    except Exception as exc:
                        attempts.append("    [object model %s%r] %s" % (method, args, exc))
                if loaded:
                    # make the ephemeris span the scenario, not the TLE epoch
                    try:
                        scenario = self.root.CurrentScenario
                        interval = scenario.Vgt.EventIntervals.Item("AnalysisInterval")
                        propagator.EphemerisInterval.SetImplicitInterval(interval)
                    except Exception:
                        try:
                            propagator.EphemerisInterval.SetExplicitInterval(start, stop)
                        except Exception:
                            pass
                    try:
                        propagator.Step = float(step_sec)
                    except Exception:
                        pass
                    propagator.Propagate()
                    if self._tle_ephemeris_ok(path, start, stop, attempts, "object model"):
                        self._tle_form = "ObjectModel/AddSegsFromFile"
                        return path
            except StkError as exc:
                attempts.append("    [object model] %s" % str(exc).splitlines()[0])
            except Exception as exc:
                attempts.append("    [object model] %s" % exc)

        # ---- route 2: Connect SetState, then propagate explicitly --------
        fields = dict(path=path, line1=line1, line2=line2,
                      step=float(step_sec), start=start or "", stop=stop or "")
        for form_name, template in self._TLE_FORMS:
            if "{start}" in template and not (start and stop):
                continue
            try:
                self.root.ExecuteCommand(template.format(**fields))
            except pywintypes.com_error as exc:
                attempts.append("    [%s] %s" % (form_name, self._com_reason(exc)))
                continue
            try:
                self.obj(path).Propagator.Propagate()
            except Exception:
                pass
            if self._tle_ephemeris_ok(path, start, stop, attempts, form_name):
                self._tle_form = form_name
                return path

        raise StkError(
            "Could not build %s from its TLE.\n  Attempts:\n%s\n"
            "  The TLE parsed as satellite %s. Check it is current and "
            "well-formed (69 characters per line)."
            % (path, "\n".join(attempts), ssc or "?"))

    #: Catalogue numbers at or above this are analyst / placeholder objects,
    #: not real tracked satellites. A generated script that could not find a
    #: real TLE tends to emit one of these -- with a VALID checksum, because
    #: the checksum is computable from the digits it invented.
    ANALYST_SSC_MIN = 90000

    #: Days between TLE epoch and scenario start beyond which SGP4 results
    #: are not trustworthy. TLEs are refreshed daily for a reason.
    TLE_EPOCH_STALE_DAYS = 45.0

    def _check_tle_plausible(self, line1, line2, ssc, start):
        """Reject a TLE that is well-formed but not credible.

        A syntactically perfect TLE tells you nothing about whether the
        satellite exists: line length and checksum are both computable, so a
        fabricated element set passes every structural test. What does not
        survive scrutiny is the catalogue number and the epoch.

        Failing here, with the reason named, beats failing four propagation
        attempts later with "produced no ephemeris" -- which reads as a bug
        in the helper rather than as bad input.
        """
        try:
            number = int("".join(c for c in ssc if c.isdigit()))
        except ValueError:
            number = None
        if number is not None and number >= self.ANALYST_SSC_MIN:
            raise StkError(
                "TLE catalogue number %s is in the analyst/placeholder range "
                "(>= %d).\n"
                "  Real tracked satellites do not have numbers up here, so "
                "this element set was\n"
                "  almost certainly invented rather than fetched. A valid "
                "checksum does not make\n"
                "  a TLE real -- the checksum is computed from whatever "
                "digits are written.\n"
                "  Fix: paste the current TLE from CelesTrak or Space-Track "
                "into the script."
                % (ssc, self.ANALYST_SSC_MIN))

        epoch = self.tle_epoch_datetime(line1)
        scenario_start = self._parse_utcg(start)
        if epoch and scenario_start:
            age_days = abs((scenario_start - epoch).total_seconds()) / 86400.0
            if age_days > self.TLE_EPOCH_STALE_DAYS:
                sys.stderr.write(
                    "[warn] TLE epoch (%s UTC) is %.0f days from the scenario "
                    "start.\n"
                    "       SGP4 error grows with epoch age; positions this far "
                    "out are indicative\n"
                    "       only, and a decaying object may not propagate at "
                    "all. Fetch a current TLE.\n"
                    % (epoch.strftime("%d %b %Y %H:%M:%S"), age_days))

    @staticmethod
    def tle_epoch_datetime(line1):
        """UTC epoch of a TLE line 1, or None if it cannot be read.

        Columns 19-32: two-digit year then day-of-year with a fraction. The
        two-digit year rolls at 57 by convention (57-99 = 19xx, 00-56 = 20xx).
        """
        try:
            year = int(line1[18:20])
            day_of_year = float(line1[20:32])
        except (ValueError, IndexError):
            return None
        year += 1900 if year >= 57 else 2000
        try:
            return (_dt.datetime(year, 1, 1)
                    + _dt.timedelta(days=day_of_year - 1.0))
        except (ValueError, OverflowError):
            return None

    @staticmethod
    def _parse_utcg(text):
        """Parse STK's UTCG string ("1 Sep 2026 00:00:00.000"), or None."""
        if not text:
            return None
        for form in ("%d %b %Y %H:%M:%S.%f", "%d %b %Y %H:%M:%S",
                     "%d %b %Y"):
            try:
                return _dt.datetime.strptime(str(text).strip(), form)
            except ValueError:
                continue
        return None

    def _tle_ephemeris_ok(self, path, start, stop, attempts, label):
        """Ephemeris must exist at both ends of the scenario window."""
        if self.orbit_radius_km(path) is None:
            attempts.append("    [%s] accepted but produced no ephemeris" % label)
            return False
        if stop and self.orbit_radius_km(path, when=stop) is None:
            attempts.append(
                "    [%s] propagated, but no ephemeris at the end of the window "
                "(%s) -- the ~1.5 h TLE propagation limit" % (label, stop))
            return False
        return True

    def next_pass(self, from_path, to_path, after=None):
        """The next access interval starting at or after ``after``.

        Returns ``(start_utcg, stop_utcg, duration_s)``, or ``None`` if there
        is no pass in the scenario's analysis window -- which usually means
        the window is too short rather than that no pass exists.
        """
        after = after or getattr(self, "_start", None)
        count, _total, intervals = self.compute_access(from_path, to_path)
        if not count:
            return None
        after_seconds = float(self.root.ConversionUtility.ConvertDate("UTCG", "EpSec", after))
        for start_time, stop_time in intervals:
            begins = float(self.root.ConversionUtility.ConvertDate("UTCG", "EpSec", start_time))
            ends = float(self.root.ConversionUtility.ConvertDate("UTCG", "EpSec", stop_time))
            if ends >= after_seconds:
                return start_time, stop_time, ends - max(begins, after_seconds)
        return None

    def pass_details(self, from_path, to_path, when_start, when_stop, step_sec=10.0):
        """Peak elevation and range for one pass, from STK's AER data.

        Reported so a pass can be checked against another tool: two
        predictions agreeing on start time but not on peak elevation usually
        means different masks or a different station position.
        """
        try:
            provider = self.obj(from_path).DataProviders.Item("AER Data")
            group = provider.Group.Item("Default") if hasattr(provider, "Group") else provider
            result = group.ExecElements(when_start, when_stop, step_sec,
                                        ["Time", "Azimuth", "Elevation", "Range"]) \
                if hasattr(group, "ExecElements") else group.Exec(when_start, when_stop, step_sec)
            datasets = result.DataSets
            elevations = [float(v) for v in datasets.GetDataSetByName("Elevation").GetValues()]
            ranges = [float(v) for v in datasets.GetDataSetByName("Range").GetValues()]
        except Exception:
            return None
        if not elevations:
            return None
        peak = max(elevations)
        return {"peak_elevation_deg": peak,
                "min_range_km": min(ranges) if ranges else None}

    def new_geo_satellite(self, name, longitude_deg, **kwargs):
        """Create a geostationary satellite parked over a given longitude.

        Placing a GEO by hand is a trap: with inclination 0 and true anomaly
        0 the satellite sits whereever Earth's rotation happens to put it at
        the epoch, which is effectively a random longitude. A relay "over
        Europe" then reports zero access to a European ground station and it
        looks like a physics result rather than a mis-placement.

        Sub-satellite longitude = right ascension - GMST, and for an
        equatorial circular orbit with RAAN and argument of perigee at zero
        the right ascension IS the true anomaly. So the true anomaly needed
        is (longitude + GMST at epoch), which is what this computes.

        ``longitude_deg`` is east-positive.
        """
        epoch = kwargs.pop("epoch", None) or kwargs.get("start") or getattr(self, "_start", None)
        if not epoch:
            raise StkError("new_geo_satellite needs an epoch: call "
                           "set_time_period() first, or pass epoch=.")
        # GMST is referenced to the equinox of date; the elements are J2000.
        # Subtracting the accumulated precession puts the satellite over the
        # requested longitude instead of ~0.34 deg east of it (in 2026).
        true_anomaly = (float(longitude_deg) + gmst_deg(epoch)
                        - precession_ra_deg(epoch)) % 360.0
        kwargs.setdefault("ecc", 0.0)
        kwargs.setdefault("arg_perigee_deg", 0.0)
        return self.new_satellite(
            name, sma_km=GEO_SMA_KM, inc_deg=0.0, raan_deg=0.0,
            true_anomaly_deg=true_anomaly, epoch=epoch, **kwargs)

    def subsatellite_point(self, sat_path, when=None):
        """Sub-satellite ``(latitude_deg, longitude_deg)``, or ``(None, None)``.

        Longitude is normalised to -180..180. Handy for putting a facility
        directly under a satellite, which makes a pointing test deterministic
        instead of depending on whether the ground track happens to pass
        anything during the analysis window.
        """
        when = when or getattr(self, "_start", None)
        if not when:
            return None, None
        try:
            provider = self.obj(sat_path).DataProviders.Item("LLA State")
            group = provider.Group.Item("Fixed") if hasattr(provider, "Group") else provider
            result = group.Exec(when, when, 60.0)
            if result.DataSets.Count == 0:
                return None, None
            latitude = float(result.DataSets.GetDataSetByName("Lat").GetValues()[0])
            longitude = float(result.DataSets.GetDataSetByName("Lon").GetValues()[0])
        except Exception:
            return None, None
        return latitude, ((longitude + 180.0) % 360.0) - 180.0

    def subsatellite_longitude_deg(self, sat_path, when=None):
        """Sub-satellite longitude in degrees east, or None.

        Used to confirm a GEO actually ended up where it was asked to be.
        """
        return self.subsatellite_point(sat_path, when)[1]

    # -- orbit state from any representation --------------------------------
    #
    # e.g. a launch provider's injection state vector, a navigation fix, or
    # elements from a paper. Uses the object model's Assign* routines on the
    # initial state, in any of STK's 21 frames. Units follow the pinned unit
    # preferences: km, km/s, degrees.

    ORBIT_STATE_FORMS = {
        "cartesian": ("AssignCartesian", ("x_km", "y_km", "z_km", "vx_kms", "vy_kms", "vz_kms")),
        "classical": ("AssignClassical", ("sma_km", "ecc", "inc_deg", "argp_deg", "raan_deg",
                                          "mean_anomaly_deg")),
        "geodetic": ("AssignGeodetic", ("lat_deg", "lon_deg", "alt_km", "lat_rate_degs",
                                        "lon_rate_degs", "alt_rate_kms")),
        "equinoctial": ("AssignEquinoctialPosigrade", ("sma_km", "h", "k", "p", "q",
                                                       "mean_lon_deg")),
        "spherical": ("AssignSpherical", ("ra_deg", "dec_deg", "radius_km", "fpa_deg",
                                          "azimuth_deg", "speed_kms")),
        "mixed_spherical": ("AssignMixedSpherical", ("lat_deg", "lon_deg", "alt_km", "fpa_deg",
                                                     "azimuth_deg", "speed_kms")),
    }

    def new_satellite_from_state(self, name, form, values, frame="J2000", epoch=None,
                                 propagator="J2Perturbation"):
        """A satellite whose initial state is given in ``form``.

        ``form``: cartesian, classical (MEAN anomaly), geodetic, equinoctial,
        spherical, mixed_spherical -- see ORBIT_STATE_FORMS for the six values
        each takes, in order. ``frame``: J2000, ICRF, TEMEOfDate,
        TrueOfDate, Fixed, ... ``epoch`` defaults to the scenario start.
        The state is read back as Cartesian and must match within 1 m and
        1 mm/s, or this raises.

        THE FRAME IS NOT OPTIONAL. MEASURED (bridge job 0094): the same six
        numbers read as J2000 vs ICRF agree to <0.1 km, but read as TEMEOfDate
        -- the frame TLE/SGP4 states come in -- they put the satellite 44.2 km
        away, and as Fixed 7,740 km away. A state vector without a stated
        frame must be asked about, never assumed J2000.
        mixed_spherical and spherical do not accept Fixed; STK lists the ten
        frames they do. Verified: a round trip through a J2000 state flies
        0.0000 km from the original after a day; a mixed-spherical injection
        over Kourou (lat 5.2, az 45) gives i = 45.23 deg vs 45.24 from
        cos i = cos(lat) sin(az).
        """
        key = str(form).lower()
        if key not in self.ORBIT_STATE_FORMS:
            raise StkError("orbit state form '%s' unknown; use one of %s"
                           % (form, ", ".join(self.ORBIT_STATE_FORMS)))
        method, labels = self.ORBIT_STATE_FORMS[key]
        if len(values) != 6:
            raise StkError("%s needs 6 values: %s" % (key, ", ".join(labels)))
        frame_enum = self.find_enum("eCoordinateSystem" + str(frame))
        if frame_enum is None:
            raise StkError("frame '%s' unknown (J2000, ICRF, TEMEOfDate, TrueOfDate, Fixed, ...)"
                           % frame)
        path = self.unique_path("*/Satellite/%s" % name)
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / */Satellite %s" % actual, label="create satellite")
        sat = self.obj(path)
        prop_enum = self.find_enum("ePropagator" + str(propagator))
        if prop_enum is None:
            raise StkError("propagator '%s' unknown" % propagator)
        sat.SetPropagatorType(prop_enum)
        prop = sat.Propagator
        try:
            prop.InitialState.Epoch = epoch or self._start
        except Exception:
            pass
        state = prop.InitialState.Representation
        try:
            getattr(state, method)(frame_enum, *[float(v) for v in values])
        except Exception as exc:
            raise StkError("%s(%s) refused: %s" % (method, frame, self._com_reason(exc)))
        prop.Propagate()
        if key == "cartesian" and frame in ("J2000", "ICRF"):
            when = epoch or self._start
            r = self.read_data(path, "Cartesian Position", group=frame, start=when, stop=when)
            v = self.read_data(path, "Cartesian Velocity", group=frame, start=when, stop=when)
            got = [r.column(c)[0] for c in ("x", "y", "z")] + [v.column(c)[0] for c in ("x", "y", "z")]
            dp = max(abs(a - float(b)) for a, b in zip(got[:3], values[:3]))
            dv = max(abs(a - float(b)) for a, b in zip(got[3:], values[3:]))
            if dp > 0.001 or dv > 1e-6:
                raise StkError("%s: state read back off by %.3f km / %.6f km/s" % (path, dp, dv))
        return path

    # -- Astrogator: manoeuvre planning ---------------------------------------
    #
    # MEASURED on STK 11, bridge jobs 0096-0112. Traps, each silent:
    #  * segment-specific members need a cast to the "_IAg..." DISPATCH
    #    interface (see gator()), with FULL generated bindings
    #  * Astrogator enums live in their own library; a missing one became
    #    None -> 0 -> four "manoeuvres" inserted as Initial State segments
    #  * an unknown propagator name ("Earth Full") is ACCEPTED and ignored --
    #    the previous propagator keeps running
    #  * a Propagate must keep >= 1 stopping condition: add before removing
    #  * a targeting goal attached to a BURN is the osculating two-body value
    #    at the burn: the targeter "converged" on apoapsis 42164.09 km while
    #    the spacecraft really reached 41,988 km. Goals belong where the
    #    physics happens -- the end of the coast.
    # Verified: point-mass propagation reproduces the analytic Hohmann
    # apoapsis (42,164.1 km, 5.2751 h vs 5.2748 h); with real gravity the
    # corrector finds DV1 2.42862 / DV2 1.46640 km/s (+2.9 m/s vs textbook
    # for J2) and the final orbit sits at 42,162-42,165 km.

    def astrogator_propagators(self):
        """Names of Astrogator's propagators on this install."""
        folder = self.root.CurrentScenario.ComponentDirectory.GetComponents(
            self.enum_strict("eComponentAstrogator")).GetFolder("Propagators")
        return [str(folder.Item(i).Name) for i in range(folder.Count)]

    def _set_gator_propagator(self, propagate_segment, name):
        valid = self.astrogator_propagators()
        if name not in valid:
            raise StkError("Astrogator propagator '%s' unknown -- STK would silently keep "
                           "the previous one. Available: %s" % (name, ", ".join(valid)))
        propagate_segment.PropagatorName = name

    def _only_stop_on(self, propagate_segment, condition, trip=None):
        conditions = propagate_segment.StoppingConditions
        if condition not in [conditions.Item(i).Name for i in range(conditions.Count)]:
            conditions.Add(condition)        # add first: the list may not go empty
        for existing in [conditions.Item(i).Name for i in range(conditions.Count)]:
            if existing != condition:
                conditions.Remove(existing)
        if trip is not None:
            stop = self.gator(conditions.Item(condition).Properties, "IAgVAStoppingCondition")
            stop.Trip = float(trip)

    def new_astrogator_satellite(self, name, sma_km, ecc=0.0, inc_deg=0.0, raan_deg=0.0,
                                 argp_deg=0.0, true_anomaly_deg=0.0,
                                 propagator="Earth HPOP Default v10"):
        """A satellite flown by Astrogator, starting from classical elements.

        Built from the elements as an inertial Cartesian state (the Initial
        State segment's default representation). Returns the path.
        """
        import math
        mu = 398600.4418
        p = sma_km * (1.0 - ecc ** 2)
        nu, i, raan, w = [math.radians(v) for v in (true_anomaly_deg, inc_deg, raan_deg, argp_deg)]
        r = p / (1.0 + ecc * math.cos(nu))
        rp = (r * math.cos(nu), r * math.sin(nu), 0.0)
        vp = (-math.sqrt(mu / p) * math.sin(nu), math.sqrt(mu / p) * (ecc + math.cos(nu)), 0.0)

        def rot(v):
            cO, sO, ci, si, cw, sw = (math.cos(raan), math.sin(raan), math.cos(i),
                                      math.sin(i), math.cos(w), math.sin(w))
            m = ((cO * cw - sO * sw * ci, -cO * sw - sO * cw * ci, sO * si),
                 (sO * cw + cO * sw * ci, -sO * sw + cO * cw * ci, -cO * si),
                 (sw * si, cw * si, ci))
            return [sum(m[a][b] * v[b] for b in range(3)) for a in range(3)]
        (x, y, z), (vx, vy, vz) = rot(rp), rot(vp)

        path = self.unique_path("*/Satellite/%s" % name)
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / */Satellite %s" % actual, label="create satellite")
        sat = self.obj(path)
        sat.SetPropagatorType(self.enum_strict("ePropagatorAstrogator"))
        mcs = sat.Propagator.MainSequence
        init = self.gator(mcs.Item("Initial State"), "IAgVAMCSInitialState")
        element = init.Element
        element.X, element.Y, element.Z = x, y, z
        element.Vx, element.Vy, element.Vz = vx, vy, vz
        self._set_gator_propagator(self.gator(mcs.Item("Propagate"), "IAgVAMCSPropagate"),
                                   propagator)
        return path

    def plan_circular_transfer(self, sat_path, target_radius_km, dv1_guess_kms=None,
                               dv2_guess_kms=None, propagator="Earth HPOP Default v10",
                               coast_after_s=86400.0, max_iterations=50):
        """Two-burn transfer from the current circular orbit to a circular orbit
        of ``target_radius_km``, SOLVED by Astrogator's differential corrector
        under the chosen propagator (real gravity by default).

        Goals: radius at the END of the coast (the real apoapsis) and zero
        eccentricity after the second burn. Returns a dict with the burns,
        total dv, iterations, and the final orbit's radius range, which is
        measured from the ephemeris -- not taken from the targeter's report.
        """
        import math
        E, G = self.enum_strict, self.gator
        drv = self.obj(sat_path).Propagator
        mcs = drv.MainSequence
        init = G(mcs.Item("Initial State"), "IAgVAMCSInitialState").Element
        r1 = math.sqrt(init.X ** 2 + init.Y ** 2 + init.Z ** 2)
        mu = 398600.4418
        at = (r1 + target_radius_km) / 2.0
        guess1 = dv1_guess_kms if dv1_guess_kms is not None else \
            math.sqrt(mu * (2 / r1 - 1 / at)) - math.sqrt(mu / r1)
        guess2 = dv2_guess_kms if dv2_guess_kms is not None else \
            math.sqrt(mu / target_radius_km) - math.sqrt(mu * (2 / target_radius_km - 1 / at))

        ts = G(mcs.Insert(E("eVASegmentTypeTargetSequence"), "Transfer", "Propagate"),
               "IAgVAMCSTargetSequence")
        inner = ts.Segments

        def impulsive(name, guess):
            seg = G(inner.Insert(E("eVASegmentTypeManeuver"), name, "-"), "IAgVAMCSManeuver")
            seg.SetManeuverType(E("eVAManeuverTypeImpulsive"))
            imp = G(seg.Maneuver, "IAgVAManeuverImpulsive")
            imp.SetAttitudeControlType(E("eVAAttitudeControlThrustVector"))
            G(imp.AttitudeControl, "IAgVAAttitudeControlImpulsiveThrustVector") \
                .DeltaVVector.AssignCartesian(guess, 0.0, 0.0)
            seg.EnableControlParameter(E("eVAControlManeuverImpulsiveCartesianX"))
            return seg

        impulsive("DV1", guess1)
        coast = G(inner.Insert(E("eVASegmentTypePropagate"), "Coast", "-"), "IAgVAMCSPropagate")
        self._set_gator_propagator(coast, propagator)
        self._only_stop_on(coast, "Apoapsis" if target_radius_km > r1 else "Periapsis")
        dv2 = impulsive("DV2", guess2 if target_radius_km > r1 else -abs(guess2))
        coast.Results.Add("Spherical Elems/R Mag")
        dv2.Results.Add("Keplerian Elems/Eccentricity")

        dc = G(ts.Profiles.Item("Differential Corrector"), "IAgVAProfileDifferentialCorrector")
        for i in range(dc.ControlParameters.Count):
            dc.ControlParameters.Item(i).Enable = True
        for i in range(dc.Results.Count):
            goal = dc.Results.Item(i)
            goal.Enable = True
            goal.DesiredValue = float(target_radius_km) if goal.ParentName == "Coast" else 0.0
        dc.MaxIterations = int(max_iterations)
        ts.Action = E("eVATargetSeqActionRunActiveProfiles")

        final = G(mcs.Item("Propagate"), "IAgVAMCSPropagate")
        self._set_gator_propagator(final, propagator)
        self._only_stop_on(final, "Duration", coast_after_s)

        drv.RunMCS()
        burns = {dc.ControlParameters.Item(i).ParentName: dc.ControlParameters.Item(i).FinalValue
                 for i in range(dc.ControlParameters.Count)}
        misses = {dc.Results.Item(i).ParentName: dc.Results.Item(i).Difference
                  for i in range(dc.Results.Count)}
        iterations = dc.NumIterations        # read BEFORE the re-run resets it
        ts.ApplyProfiles()
        drv.RunMCS()
        if abs(misses.get("Coast", 1e9)) > 1.0 or abs(misses.get("DV2", 1.0)) > 1e-4:
            raise StkError("transfer did not converge: misses %r after %s iterations"
                           % (misses, iterations))
        # Measure the final orbit AFTER the second burn -- timed from the
        # transfer itself, not from the scenario window. MEASURED (smoke test):
        # in a 6-hour scenario the "last stretch of the window" still held the
        # starting LEO, and the check read 6678-42164 km.
        start = str(self.root.CurrentScenario.StartTime)
        transfer_s = math.pi * math.sqrt(at ** 3 / mu)
        window = (self._utcg_offset(start, transfer_s + 600.0),
                  self._utcg_offset(start, transfer_s + max(1200.0, float(coast_after_s) - 60.0)))
        data = self.read_data(sat_path, "Cartesian Position", group="J2000",
                              start=window[0], stop=window[1], step_sec=60.0)
        if data.empty:
            raise StkError("no ephemeris after the transfer (%s) -- %s"
                           % (window[0], data.message))
        tail = [math.sqrt(x * x + y * y + z * z) for x, y, z in
                zip(data.column("x"), data.column("y"), data.column("z"))]
        return {"dv1_kms": burns.get("DV1"), "dv2_kms": burns.get("DV2"),
                "total_dv_kms": abs(burns.get("DV1", 0)) + abs(burns.get("DV2", 0)),
                "textbook_dv_kms": (abs(guess1) + abs(guess2)) if dv1_guess_kms is None else None,
                "iterations": iterations,
                "final_radius_km": (min(tail), max(tail)),
                "propagator": propagator}

    # -- Astrogator: finite burns ---------------------------------------------
    #
    # MEASURED on STK 11, bridge jobs 0120-0122:
    #  * the built-in engine "Constant Thrust and Isp" (500 N, 300 s) is
    #    READ-ONLY: work on a CloneObject() copy and point the burn at it
    #  * UpdateMass on a finite burn raises ("deprecated for finite")
    #  * fuel used == thrust*t/(Isp*g0) exactly; dv follows the rocket equation;
    #    a 1500 s / 4 N burn raised SMA 5 m less than the impulsive equivalent
    #    (gravity loss) -- STK models it, nothing to correct

    def _engine(self, thrust_n, isp_s):
        """Name of an editable constant engine with this thrust and Isp."""
        E, G = self.enum_strict, self.gator
        folder = self.root.CurrentScenario.ComponentDirectory.GetComponents(
            E("eComponentAstrogator")).GetFolder("Engine Models")
        names = lambda: [str(folder.Item(i).Name) for i in range(folder.Count)]
        for n in names():
            if n.startswith("Constant Thrust and Isp") and n != "Constant Thrust and Isp":
                try:
                    e = G(folder.Item(n), "IAgVAEngineConstant")
                    if abs(e.Thrust - thrust_n) < 1e-9 and abs(e.Isp - isp_s) < 1e-9:
                        return n
                except Exception:
                    pass
        before = names()
        G(folder.Item("Constant Thrust and Isp"), "IAgVAEngineConstant").CloneObject()
        new = [n for n in names() if n not in before][0]
        e = G(folder.Item(new), "IAgVAEngineConstant")
        e.Thrust, e.Isp = float(thrust_n), float(isp_s)
        return new

    def finite_burn(self, sat_path, thrust_n, isp_s, duration_s=None, dv_ms=None,
                    direction="prograde", name="Burn", before="Propagate",
                    propagator=None):
        """Insert a finite burn along (or against) the velocity vector.

        Give ``duration_s`` or ``dv_ms`` (the duration is then solved from the
        rocket equation with the CURRENT spacecraft mass). Does not run the
        MCS. Returns (segment, duration_s, engine name).
        """
        import math
        E, G = self.enum_strict, self.gator
        mcs = self.obj(sat_path).Propagator.MainSequence
        if (duration_s is None) == (dv_ms is None):
            raise StkError("give exactly one of duration_s or dv_ms")
        if duration_s is None:
            init = G(mcs.Item("Initial State"), "IAgVAMCSInitialState")
            m0 = (G(init.SpacecraftParameters, "IAgVASpacecraftParameters").DryMass
                  + G(init.FuelTank, "IAgVAFuelTank").FuelMass)
            ve = isp_s * 9.80665
            used = m0 * (1.0 - math.exp(-abs(dv_ms) / ve))
            fuel = G(init.FuelTank, "IAgVAFuelTank").FuelMass
            if used > fuel:
                raise StkError("dv %.1f m/s needs %.2f kg of fuel; tank holds %.2f kg"
                               % (dv_ms, used, fuel))
            duration_s = used * ve / thrust_n
        seg = G(mcs.Insert(E("eVASegmentTypeManeuver"), name, before), "IAgVAMCSManeuver")
        seg.SetManeuverType(E("eVAManeuverTypeFinite"))
        fin = G(seg.Maneuver, "IAgVAManeuverFinite")
        att = {"prograde": "eVAAttitudeControlVelocityVector",
               "retrograde": "eVAAttitudeControlAntiVelocityVector"}.get(direction)
        if att is None:
            raise StkError("direction must be 'prograde' or 'retrograde'")
        fin.SetAttitudeControlType(E(att))
        engine = self._engine(thrust_n, isp_s)
        fin.SetPropulsionMethod(E("eVAPropulsionMethodEngineModel"), engine)
        fp = G(fin.Propagator, "IAgVAManeuverFinitePropagator")
        if propagator:
            self._set_gator_propagator(fp, propagator)
        self._only_stop_on(fp, "Duration", duration_s)
        return seg, duration_s, engine

    # -- STK's Orbit Wizard ---------------------------------------------------
    #
    # Not in any type library (checked all 97): it is the Connect command
    # OrbitWizard. MEASURED on STK 11, bridge jobs 0078-0080:
    #   * distances are in METRES and a wrong unit is accepted silently --
    #     "Altitude 600" built a satellite 0.6 km above the ground
    #   * local times must be "HH:MM:SS" (10.5, 10:30, 37800 are rejected)
    #   * SunSynchronous without a local time gives a noon/midnight plane
    #   * RepeatingSunSync ACCEPTS a local time and IGNORES it
    #   * orbits must be checked in TRUE-OF-DATE axes: an SSO reads 97.65 deg
    #     in J2000 but 97.792 deg in TOD, matching J2 theory (97.79)
    # Verified: GEO over 23.700 E exactly; Molniya perigee 600.0 km; critically
    # inclined 116.565 deg; repeating ground track back within 34.5 km/day.

    WIZARD_TYPES = ("SunSynchronous", "RepeatingSunSync", "RepeatingGroundTrace",
                    "Circular", "Geosynchronous", "Molniya", "CriticallyInclined",
                    "CriticallyInclinedSunSync", "OrbitDesigner")
    _WIZARD_KM = ("Altitude", "ApogeeAltitude", "PerigeeAltitude", "ApproxAltitude",
                  "SemimajorAxis")
    _WIZARD_TIME = ("LocalTimeAscNode", "LocalTimeDescNode")

    @staticmethod
    def _clock(value):
        """10.5, "10.5", "10:30" or "10:30:00" -> "10:30:00"."""
        text = str(value).strip()
        if ":" in text:
            parts = [float(x) for x in text.split(":")]
            hours = parts[0] + parts[1] / 60.0 + (parts[2] / 3600.0 if len(parts) > 2 else 0.0)
        else:
            hours = float(text)
        if not 0.0 <= hours < 24.0:
            raise StkError("local time %r is not between 00:00 and 24:00" % value)
        total = int(round(hours * 3600.0))
        return "%02d:%02d:%02d" % (total // 3600, (total % 3600) // 60, total % 60)

    def local_time_of_nodes(self, satellite_path, when=None):
        """(LTAN, LTDN) in hours, from the orbit plane and STK's own Sun vector.

        Uses the TRUE Sun, so it differs from a mean-solar-time design value
        by the equation of time (about -4 to +16 minutes over the year).
        """
        import math
        when = when or self._start
        elements = self.read_data(satellite_path, "Classical Elements", group="TrueOfDate",
                                  elements=["Time", "RAAN"], start=when, stop=when)
        sun = self.read_data(satellite_path, "Vectors(Inertial)", group="Sun",
                             start=when, stop=when)
        cols = {c.lower(): sun.column(c)[0] for c in sun.columns}
        ra_sun = math.degrees(math.atan2(cols["y"], cols["x"])) % 360.0
        ltan = (12.0 + ((elements.column("RAAN")[0] - ra_sun + 540.0) % 360.0 - 180.0) / 15.0) % 24.0
        return ltan, (ltan - 12.0) % 24.0

    def orbit_wizard(self, name, kind, **params):
        """A satellite from STK's Orbit Wizard. Distances in KILOMETRES.

        ``kind`` and its keywords (STK 11):

          SunSynchronous        altitude_km | inclination_deg, and
                                local_time_desc_node | local_time_asc_node (REQUIRED)
          RepeatingSunSync      approx_altitude_km | approx_revs_per_day,
                                revs_to_repeat, longitude_first_an_deg
          RepeatingGroundTrace  approx_altitude_km | approx_revs_per_day,
                                inclination_deg, revs_to_repeat, longitude_first_an_deg
          Circular              inclination_deg, altitude_km, raan_deg
          Geosynchronous        subsatellite_point_deg, inclination_deg
          Molniya               apogee_longitude_deg, perigee_altitude_km,
                                argument_of_perigee_deg
          CriticallyInclined    direction ("Posigrade"/"Retrograde"),
                                apogee_altitude_km, perigee_altitude_km, lan_deg
          CriticallyInclinedSunSync  perigee_altitude_km, lan_deg
          OrbitDesigner         semimajor_axis_km, eccentricity, inclination_deg,
                                argument_of_perigee_deg, raan_deg, true_anomaly_deg

        Local times accept 10.5, "10:30" or "10:30:00". A sun-synchronous
        result is measured afterwards and refused if its local time is off by
        more than 10 minutes (RepeatingSunSync ignores the requested time).
        """
        canon = {k.lower(): k for k in self.WIZARD_TYPES}
        if str(kind).lower() not in canon:
            raise StkError("Orbit Wizard type '%s' unknown. Types: %s"
                           % (kind, ", ".join(self.WIZARD_TYPES)))
        kind = canon[str(kind).lower()]
        if kind == "SunSynchronous" and not any(k.startswith("local_time") for k in params):
            raise StkError("SunSynchronous needs local_time_desc_node or "
                           "local_time_asc_node -- STK's default is a noon/midnight plane.")
        keyword = {
            "altitude_km": "Altitude", "inclination_deg": "Inclination", "raan_deg": "RAAN",
            "local_time_desc_node": "LocalTimeDescNode", "local_time_asc_node": "LocalTimeAscNode",
            "approx_altitude_km": "ApproxAltitude", "approx_revs_per_day": "ApproxRevsPerDay",
            "revs_to_repeat": "RevsToRepeat", "longitude_first_an_deg": "LongitudeFirstAN",
            "subsatellite_point_deg": "SubsatellitePoint", "apogee_longitude_deg": "ApogeeLongitude",
            "perigee_altitude_km": "PerigeeAltitude", "apogee_altitude_km": "ApogeeAltitude",
            "argument_of_perigee_deg": "ArgumentOfPerigee", "direction": "Direction",
            "lan_deg": "LAN", "semimajor_axis_km": "SemimajorAxis", "eccentricity": "Eccentricity",
            "true_anomaly_deg": "TrueAnomaly",
        }
        parts = []
        wanted_time = None
        for key, value in params.items():
            if key not in keyword:
                raise StkError("unknown Orbit Wizard parameter '%s'. Known: %s"
                               % (key, ", ".join(sorted(keyword))))
            word = keyword[key]
            if word in self._WIZARD_KM:
                km = float(value)
                if km > 200000:
                    raise StkError("%s=%s km is implausible -- give kilometres, not metres"
                                   % (key, value))
                parts.append("%s %.3f" % (word, km * 1000.0))
            elif word in self._WIZARD_TIME:
                clock = self._clock(value)
                wanted_time = (word, clock)
                parts.append("%s %s" % (word, clock))
            else:
                parts.append("%s %s" % (word, value))

        path = self.unique_path("*/Satellite/%s" % name)
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / */Satellite %s" % actual, label="create satellite")
        self.cmd("OrbitWizard %s %s %s" % (path, kind, " ".join(parts)),
                 label="Orbit Wizard %s" % kind)

        radius = self.orbit_radius_km(path)
        if radius is None:
            raise StkError("Orbit Wizard %s produced no ephemeris for %s" % (kind, path))
        elements = self.read_data(path, "Classical Elements", group="TrueOfDate",
                                  elements=["Time", "Semi-major Axis", "Eccentricity"],
                                  start=self._start, stop=self._start)
        a, e = elements.column("Semi-major Axis")[0], elements.column("Eccentricity")[0]
        perigee_alt = a * (1.0 - e) - self.body_radius_km("Earth")
        if perigee_alt < 100.0:
            raise StkError("%s: perigee altitude %.1f km -- below any real orbit. A "
                           "distance was probably given in the wrong unit." % (path, perigee_alt))

        if wanted_time and kind in ("SunSynchronous", "RepeatingSunSync"):
            import math
            ltan, ltdn = self.local_time_of_nodes(path)
            got = ltdn if wanted_time[0] == "LocalTimeDescNode" else ltan
            h, m, sec = [int(x) for x in wanted_time[1].split(":")]
            asked = h + m / 60.0 + sec / 3600.0
            off_min = abs(((got - asked + 12.0) % 24.0) - 12.0) * 60.0
            # the wizard designs in MEAN solar time; up to ~17 min of equation
            # of time separates it from the true Sun used here
            if off_min > 20.0:
                raise StkError(
                    "%s: asked %s = %s, but the orbit plane sits at %.2f h (%.0f min "
                    "off). STK 11's %s ignores the local time; use SunSynchronous "
                    "or set the RAAN afterwards." % (path, wanted_time[0], wanted_time[1],
                                                     got, off_min, kind))
        return path

    # -- STK's Walker tool (seed-based constellations) ------------------------
    #
    # MEASURED on STK 11 (bridge jobs 0114-0115), Connect "Walker":
    #  * Delta 12/3/1 is identical, satellite by satellite, to new_walker()
    #  * Star spreads planes over 180 deg (6 planes -> 0, 30 ... 150)
    #  * the seed's sub-objects (sensors, radios) are COPIED to every member
    #  * members are named <seed><plane><position>, e.g. Sat11 ... Sat34
    #  * the SEED SURVIVES, in the same place as member 11 -- every
    #    "all satellites" analysis would count that satellite twice. It is
    #    unloaded here; members keep their payloads and ephemeris.

    def new_constellation(self, name, members):
        """A Constellation object grouping ``members`` (paths). Returns its path.

        Coverage, chains and interference studies can take the whole
        constellation as ONE asset instead of N satellites.
        """
        return self._constellation_of(name, members)

    def walker_constellation(self, seed_path, kind="Delta", num_planes=None,
                             sats_per_plane=None, total_sats=None, phase_f=0,
                             constellation_name=None, keep_seed=False):
        """Replicate a fully equipped seed satellite into a Walker constellation.

        ``kind``: "Delta" (planes over 360 deg of RAAN) or "Star" (over 180,
        e.g. Iridium-like polar). The seed's orbit sets altitude, inclination
        and the first plane; its sensors and radios are copied to every
        member. The seed itself is removed unless ``keep_seed`` (it would
        duplicate member 11). Returns (member paths, constellation path).
        """
        kind = str(kind).capitalize()
        if kind not in ("Delta", "Star"):
            raise StkError("Walker kind must be Delta or Star (got %s)" % kind)
        if not num_planes or not (sats_per_plane or total_sats):
            raise StkError("give num_planes and sats_per_plane or total_sats")
        if not 0 <= int(phase_f) < int(num_planes):
            raise StkError("phase_f must be 0..num_planes-1 (got %s)" % phase_f)
        name = constellation_name or seed_path.rsplit("/", 1)[-1] + "_Walker"
        count = ("NumSatsPerPlane %d" % int(sats_per_plane)) if sats_per_plane \
            else ("TotalNumSats %d" % int(total_sats))
        seed_name = seed_path.rsplit("/", 1)[-1]
        sat_type = self.enum_strict("eSatellite")

        def satellites():
            return {str(c.InstanceName) for c in self.root.CurrentScenario.Children
                    if int(c.ClassType) == sat_type}
        before = satellites()
        self.cmd("Walker %s Type %s NumPlanes %d %s InterPlanePhaseIncrement %d "
                 "ConstellationName %s" % (seed_path, kind, int(num_planes), count,
                                          int(phase_f), name), label="Walker %s" % kind)
        # satellites only: the Constellation object is new too, and its name
        # starts with the seed's
        made = sorted(n for n in (satellites() - before) if n.startswith(seed_name))
        members = ["*/Satellite/" + n for n in made]
        expected = int(sats_per_plane) * int(num_planes) if sats_per_plane else int(total_sats)
        if len(members) != expected:
            raise StkError("Walker made %d satellites, expected %d" % (len(members), expected))
        # verify the plane spread from the members' own elements
        when = self._start
        raans = set()
        for path in members:
            r = self.read_data(path, "Classical Elements", group="J2000",
                               elements=["Time", "RAAN"], start=when, stop=when)
            raans.add(round(r.column("RAAN")[0] % 360.0, 3))
        if len(raans) != int(num_planes):
            raise StkError("Walker produced %d distinct planes, expected %d" % (len(raans), num_planes))
        spread = 360.0 if kind == "Delta" else 180.0
        ordered = sorted(raans)
        steps = {round((b - a) % 360.0, 2) for a, b in zip(ordered, ordered[1:])}
        if int(num_planes) > 1 and steps != {round(spread / int(num_planes), 2)}:
            raise StkError("plane spacing %s, expected %.2f for %s"
                           % (sorted(steps), spread / int(num_planes), kind))
        if not keep_seed:
            self.cmd("Unload / %s" % seed_path, label="remove Walker seed")
        return members, "*/Constellation/%s" % name

    def new_walker(self, prefix, total_sats, num_planes, phase_f,
                   sma_km, inc_deg, **kwargs):
        """Create a Walker Delta constellation. Returns a list of paths.

        RAAN and true anomaly are COMPUTED per satellite from the T/P/F
        geometry -- do not pass them. To rotate the whole constellation, pass
        ``raan0_deg`` and/or ``true_anom0_deg``.

        Any other keyword argument is forwarded to :meth:`new_satellite`
        (``propagator``, ``ecc``, ``arg_perigee_deg``, ``coord_system``,
        ``central_body``, ``step_sec``, ...).
        """
        for single, offset in self._WALKER_ALIASES.items():
            if single in kwargs:
                value = kwargs.pop(single)
                sys.stderr.write(
                    "[info] new_walker computes %s per satellite; treating "
                    "%s=%s as the constellation offset %s instead.\n"
                    % (single, single, value, offset))
                kwargs.setdefault(offset, value)

        raan0 = kwargs.pop("raan0_deg", 0.0)
        true_anom0 = kwargs.pop("true_anom0_deg", 0.0)

        paths = []
        for index, _plane, raan, true_anom in walker_delta_elements(
                total_sats, num_planes, phase_f,
                raan0_deg=raan0, true_anom0_deg=true_anom0):
            paths.append(self.new_satellite(
                "%s%d" % (prefix, index + 1), sma_km=sma_km, inc_deg=inc_deg,
                raan_deg=raan, true_anomaly_deg=true_anom, **kwargs))
        return paths

    #: Verified against a real STK 11 install by discover_stk_api.py.
    #: Findings that drove this list, and that are easy to get wrong:
    #:   * ``UseScenarioInterval`` works; the ``TimePeriod <start> <stop>``
    #:     form was rejected in every combination tried.
    #:   * SetState wants the semi-major axis in METRES, and ignores the
    #:     Distance unit preference -- even though SetPosition honours it.
    #:   * J2000 and ICRF are both accepted; Fixed is not.
    #: The forms are still probed in order and the winner cached, so a build
    #: that differs degrades gracefully instead of failing outright.
    _CLASSICAL_FORMS = (
        ("UseScenarioInterval/metres",
         'SetState {path} Classical {prop} UseScenarioInterval {step:g} '
         '{coord} "{epoch}" {sma_m:.6f} {ecc:.12f} {inc:.9f} {argp:.9f} '
         '{raan:.9f} {ta:.9f}'),
        ("UseScenarioInterval/km",
         'SetState {path} Classical {prop} UseScenarioInterval {step:g} '
         '{coord} "{epoch}" {sma_km:.9f} {ecc:.12f} {inc:.9f} {argp:.9f} '
         '{raan:.9f} {ta:.9f}'),
        ("TimePeriod/metres",
         'SetState {path} Classical {prop} TimePeriod "{start}" "{stop}" {step:g} '
         '{coord} "{epoch}" {sma_m:.6f} {ecc:.12f} {inc:.9f} {argp:.9f} '
         '{raan:.9f} {ta:.9f}'),
    )

    def _set_classical_state(self, path, propagator, start, stop, step_sec,
                             coord_system, epoch, sma_km, ecc, inc_deg,
                             arg_perigee_deg, raan_deg, true_anomaly_deg):
        """Set classical elements and propagate, then VERIFY the result.

        Verification matters more than it looks. A SetState that STK accepts
        but interprets in the wrong distance unit produces a scenario that
        looks fine and is wrong by a factor of 1000. So after each accepted
        form the orbit radius is read back and compared with what was asked
        for; a mismatch is treated as a rejection and the next form is tried.
        """
        fields = dict(path=path, prop=propagator, start=start, stop=stop,
                      step=float(step_sec), coord=coord_system, epoch=epoch,
                      sma_km=float(sma_km), sma_m=float(sma_km) * 1000.0,
                      ecc=float(ecc), inc=float(inc_deg),
                      argp=float(arg_perigee_deg), raan=float(raan_deg),
                      ta=float(true_anomaly_deg))

        cached = getattr(self, "_classical_form", None)
        forms = self._CLASSICAL_FORMS
        if cached is not None:
            forms = tuple(f for f in forms if f[0] == cached) or forms

        attempts = []
        for name, template in forms:
            try:
                self.root.ExecuteCommand(template.format(**fields))
            except pywintypes.com_error as exc:
                attempts.append("    [%s] rejected: %s" % (name, self._com_reason(exc)))
                continue

            radius = self.orbit_radius_km(path)
            if radius is None:
                attempts.append("    [%s] accepted but produced no ephemeris" % name)
                continue
            # The radius must lie between perigee and apogee, with 20% slack
            # for the epoch not being exactly at either. Using a band around
            # the semi-major axis instead would reject every eccentric orbit:
            # a Molniya at perigee sits at ~0.26a, which is a correct orbit,
            # not a unit error. A real unit error is off by ~1000x and still
            # fails this.
            eccentricity = min(abs(float(ecc)), 0.95)
            perigee = float(sma_km) * (1.0 - eccentricity) * 0.8
            apogee = float(sma_km) * (1.0 + eccentricity) * 1.2
            if not (perigee <= radius <= apogee):
                attempts.append(
                    "    [%s] accepted but orbit radius read back as %.1f km, "
                    "outside the %.1f-%.1f km perigee/apogee band for a=%.1f e=%.3f"
                    % (name, radius, perigee, apogee, sma_km, eccentricity))
                continue

            self._classical_form = name
            return True

        raise StkError(
            "Could not set a correct classical orbit state on %s.\n"
            "  Attempts:\n%s\n"
            "  The satellite object exists but has no usable ephemeris, so "
            "access and reports would be empty or wrong." % (path, "\n".join(attempts)))

    def orbit_radius_km(self, sat_path, when=None):
        """Orbit radius in km at ``when`` (default: ephemeris start), or None.

        Used to verify that a satellite really has ephemeris AND that it came
        out at the intended scale.
        """
        when = when or getattr(self, "_start", None)
        if not when:
            return None
        try:
            provider = self.obj(sat_path).DataProviders.Item("Cartesian Position")
            group = provider.Group.Item("ICRF") if hasattr(provider, "Group") else provider
            result = group.Exec(when, when, 60.0)
            datasets = result.DataSets
            if datasets.Count == 0:
                # A ZERO-LENGTH interval returns no samples for a satellite
                # propagated from TLE segments (SGP4), though it works for
                # J2/TwoBody. That made new_satellite_from_tle report "no
                # ephemeris" for satellites that had propagated perfectly --
                # the import was fine, this readback was not. Ask for a real
                # span and take its first sample.
                later = self._utcg_offset(when, 60.0)
                if later:
                    result = group.Exec(when, later, 60.0)
                    datasets = result.DataSets
            if datasets.Count == 0:
                return None
            x = float(datasets.GetDataSetByName("x").GetValues()[0])
            y = float(datasets.GetDataSetByName("y").GetValues()[0])
            z = float(datasets.GetDataSetByName("z").GetValues()[0])
        except Exception:
            return None
        return (x * x + y * y + z * z) ** 0.5

    def _utcg_offset(self, when, seconds):
        """``when`` shifted by ``seconds``, as a UTCG string, or None.

        Uses STK's own date conversion so the calendar arithmetic matches
        whatever the scenario is using.
        """
        try:
            convert = self.root.ConversionUtility.ConvertDate
            epoch_seconds = float(convert("UTCG", "EpSec", when))
            return str(convert("EpSec", "UTCG", "%.6f" % (epoch_seconds + seconds)))
        except Exception:
            return None

    # -- locating STK's own data files -------------------------------------
    def directory(self, which="STKHome"):
        """Return one of STK's own directories, or None.

        Uses the Connect ``GetDirectory`` command, so the answer comes from
        the running STK rather than a guessed install path. Useful values:
        "STKHome", "Config", "Database", "UserFolder".
        """
        try:
            result = self.root.ExecuteCommand("GetDirectory / %s" % which)
        except Exception:
            return None
        for accessor in (lambda r: r.Item(0), lambda r: r[0]):
            try:
                value = str(accessor(result)).strip()
                if value:
                    return value
            except Exception:
                continue
        return None

    #: Where gravity files live relative to STK's home directory. Ordered
    #: most-likely first; central-body subfolders are appended at runtime.
    _GRAVITY_SUBDIRS = (
        os.path.join("STKData", "CentralBodies"),
        os.path.join("STKData", "CentralBodies", "Earth"),
        os.path.join("STKData", "CentralBodies", "Moon"),
        os.path.join("STKData", "CentralBodies", "Mars"),
        "STKData",
    )

    def find_data_file(self, filename, central_body=None):
        """Resolve a bare STK data filename (e.g. "GL0420A.grv") to a path.

        A generated script cannot know where STK keeps its gravity files --
        the install root differs per machine and per version -- so asking the
        caller for an absolute path guarantees a "file does not exist" error
        the caller had no way to avoid. This asks STK where it lives and
        searches the standard data folders underneath.

        Returns an absolute path, or None if nothing matched.
        """
        if os.path.isabs(filename) and os.path.isfile(filename):
            return filename

        home = self.directory("STKHome")
        roots = [home] if home else []
        # Fall back to the usual install locations if GetDirectory is
        # unavailable on this build.
        roots += [r"C:\Program Files\AGI\STK 12", r"C:\Program Files\AGI\STK 11",
                  r"C:\Program Files (x86)\AGI\STK 11"]

        searched = []
        for root_dir in roots:
            if not root_dir or not os.path.isdir(root_dir):
                continue
            subdirs = list(self._GRAVITY_SUBDIRS)
            if central_body:
                subdirs.insert(0, os.path.join("STKData", "CentralBodies", str(central_body)))
            for subdir in subdirs:
                candidate = os.path.join(root_dir, subdir, filename)
                searched.append(candidate)
                if os.path.isfile(candidate):
                    return candidate
            # last resort: walk the data tree once
            data_root = os.path.join(root_dir, "STKData")
            if os.path.isdir(data_root):
                for dirpath, _dirnames, filenames in os.walk(data_root):
                    if filename in filenames:
                        return os.path.join(dirpath, filename)
        self._last_search = searched
        return None

    # -- HPOP force model -----------------------------------------------------
    #
    # MEASURED on STK 11 (bridge job 0082): a new HPOP satellite already has a
    # full force model -- EGM2008 21x21, solid tides, drag ON, SRP ON, Sun and
    # Moon third bodies -- but drag and SRP use GENERIC spacecraft values
    # (Cd 2.2, area/mass 0.02 m^2/kg). Decay scales directly with area/mass, so
    # an HPOP run without the real spacecraft's values silently models some
    # other satellite.
    # Plane drift of a 600 km SSO: J2 +0.9852, J4 +0.9885, HPOP +0.9893 deg/day
    # vs +0.9856 target -- a J2-designed SSO drifts ~5 min of local time a year.

    #: Spacecraft presets for drag and SRP. Area is the TUMBLING-AVERAGE
    #: cross-section (total surface / 4, exact for a convex body in random
    #: orientation) -- the standard assumption when attitude is not controlled.
    #: Deployable solar arrays can double the area: use spacecraft="custom".
    SPACECRAFT_PRESETS = {
        "1U":  {"mass_kg": 1.33, "dims_m": (0.10, 0.10, 0.1135)},
        "3U":  {"mass_kg": 4.0,  "dims_m": (0.10, 0.10, 0.34)},
        "6U":  {"mass_kg": 12.0, "dims_m": (0.10, 0.226, 0.366)},
        "12U": {"mass_kg": 24.0, "dims_m": (0.226, 0.226, 0.34)},
        "microsat100": {"mass_kg": 100.0, "dims_m": (0.60, 0.60, 0.60)},
    }

    @staticmethod
    def spacecraft_area_mass(mass_kg, dims_m=None, area_m2=None):
        """Area/mass (m^2/kg) from mass and either box dimensions or an area.

        With dimensions, the area is the tumbling average: surface area / 4.
        """
        if area_m2 is None:
            if not dims_m or len(dims_m) != 3:
                raise StkError("give dims_m=(x, y, z) in metres or area_m2")
            x, y, z = [float(v) for v in dims_m]
            area_m2 = 2.0 * (x * y + y * z + x * z) / 4.0
        if float(mass_kg) <= 0:
            raise StkError("mass must be positive")
        return float(area_m2) / float(mass_kg)

    def resolve_spacecraft(self, spacecraft=None, mass_kg=None, dims_m=None, area_m2=None):
        """(area/mass, description) for a preset name or a custom spacecraft."""
        if spacecraft and str(spacecraft).lower() != "custom":
            key = next((k for k in self.SPACECRAFT_PRESETS
                        if k.lower() == str(spacecraft).lower()), None)
            if key is None:
                raise StkError("spacecraft '%s' unknown. Presets: %s, or 'custom' with "
                               "mass_kg and dims_m/area_m2"
                               % (spacecraft, ", ".join(self.SPACECRAFT_PRESETS)))
            preset = self.SPACECRAFT_PRESETS[key]
            amr = self.spacecraft_area_mass(preset["mass_kg"], dims_m=preset["dims_m"])
            return amr, ("%s preset: %.2f kg, %s m, tumbling area/mass %.4f m^2/kg"
                         % (key, preset["mass_kg"],
                            "x".join("%.3g" % d for d in preset["dims_m"]), amr))
        if mass_kg is None:
            return None, None
        amr = self.spacecraft_area_mass(mass_kg, dims_m=dims_m, area_m2=area_m2)
        return amr, ("custom: %.2f kg, %s, area/mass %.4f m^2/kg"
                     % (float(mass_kg),
                        ("area %.4f m^2" % area_m2) if area_m2 is not None
                        else "%s m tumbling" % "x".join("%.3g" % d for d in dims_m), amr))

    def spacecraft_mass_area(self, spacecraft=None, mass_kg=None, dims_m=None, area_m2=None):
        """(mass kg, tumbling area m^2, description) for a preset or custom craft."""
        if spacecraft and str(spacecraft).lower() != "custom":
            key = next((k for k in self.SPACECRAFT_PRESETS
                        if k.lower() == str(spacecraft).lower()), None)
            if key is None:
                raise StkError("spacecraft '%s' unknown. Presets: %s"
                               % (spacecraft, ", ".join(self.SPACECRAFT_PRESETS)))
            mass_kg, dims_m = self.SPACECRAFT_PRESETS[key]["mass_kg"], \
                self.SPACECRAFT_PRESETS[key]["dims_m"]
        if mass_kg is None:
            raise StkError("give spacecraft=<preset> or mass_kg with dims_m/area_m2")
        if area_m2 is None:
            if not dims_m:
                raise StkError("give dims_m=(x, y, z) in metres or area_m2")
            x, y, z = [float(v) for v in dims_m]
            area_m2 = 2.0 * (x * y + y * z + x * z) / 4.0
        amr = float(area_m2) / float(mass_kg)
        return float(mass_kg), float(area_m2), ("%.2f kg, area %.4f m^2, area/mass %.4f m^2/kg"
                                                % (float(mass_kg), float(area_m2), amr))

    DENSITY_MODELS = {"msis00": "eMSIS00", "msis90": "eMSIS90", "msis86": "eMSIS86",
                      "jacchia-roberts": "eJacchiaRoberts", "jacchia70": "eJacchia70",
                      "jacchia71": "eJacchia71", "jacchia60": "eJacchia60",
                      "harris-priester": "eHarrisPriester", "cira72": "eCira72",
                      "exponential": "eExponentialModel", "1976": "e1976StandardAtmosModel"}

    def set_hpop_forces(self, sat_path, drag=True, cd=2.2, drag_area_mass=None,
                        spacecraft=None, mass_kg=None, dims_m=None, area_m2=None,
                        srp=True, cr=1.3, srp_area_mass=None, density_model=None,
                        f107=None, f107_avg=None, kp=None, third_bodies=("Sun", "Moon"),
                        gravity_degree=None, propagate=True):
        """Set an HPOP satellite's forces FROM THE REAL SPACECRAFT.

        ``spacecraft`` names a preset ("1U", "3U", "6U", "12U", "microsat100")
        or "custom" with ``mass_kg`` and ``dims_m``=(x, y, z) or ``area_m2``;
        the area/mass used is printed so the assumption is always visible.
        ``drag_area_mass`` / ``srp_area_mass`` are m^2/kg -- cross-section area
        over mass. Typical: 1U cubesat ~0.01 (0.01 m^2 / 1.33 kg is 0.0075),
        3U ~0.008-0.03 depending on attitude, 100 kg smallsat ~0.005-0.01.
        Leaving them unset keeps STK's generic 0.02 and prints a warning.
        ``density_model`` one of DENSITY_MODELS (default: keep STK's).
        ``f107``/``f107_avg``/``kp`` fix the solar/geomagnetic activity by hand;
        decay rates at LEO vary several-fold across the solar cycle.
        ``kp`` is the Kp index, 0-9 -- NOT Ap. MEASURED (job 0083): STK's
        GeomagneticIndex accepts 0-10, so an Ap value such as 5 would be taken
        silently as Kp 5, a geomagnetic storm. Quiet ~1-2, moderate ~3.
        """
        if drag_area_mass is None and (spacecraft or mass_kg is not None):
            drag_area_mass, described = self.resolve_spacecraft(spacecraft, mass_kg,
                                                                 dims_m, area_m2)
            sys.stderr.write("[assume] %s spacecraft -- %s\n" % (sat_path, described))
        prop = self.obj(sat_path).Propagator
        try:
            self._apply_hpop_forces(prop, drag, cd, drag_area_mass, srp, cr, srp_area_mass,
                                    density_model, f107, f107_avg, kp, third_bodies,
                                    gravity_degree, sat_path)
        except Exception:
            # MEASURED (bridge jobs 0083/0086): a force setting changes the
            # object's properties but NOT its ephemeris until it propagates. A
            # failure halfway left a satellite reporting area/mass 0.01 while
            # its trajectory had been computed with 0.02. Never leave it so.
            try:
                prop.Propagate()
            except Exception:
                pass
            sys.stderr.write("[warn] %s: force settings only PARTLY applied; the "
                             "ephemeris was recomputed with what did apply.\n" % sat_path)
            raise
        if propagate:
            prop.Propagate()
        return sat_path

    def _apply_hpop_forces(self, prop, drag, cd, drag_area_mass, srp, cr, srp_area_mass,
                           density_model, f107, f107_avg, kp, third_bodies,
                           gravity_degree, sat_path):
        forces = prop.ForceModel
        forces.Drag.Use = bool(drag)
        if drag:
            model = forces.Drag.DragModel
            model.Cd = float(cd)
            if drag_area_mass is None:
                sys.stderr.write(
                    "[warn] %s drag uses STK's generic area/mass 0.02 m^2/kg -- pass "
                    "drag_area_mass for the real spacecraft; decay scales with it.\n" % sat_path)
            else:
                model.AreaMassRatio = float(drag_area_mass)
            if density_model:
                key = str(density_model).lower()
                if key not in self.DENSITY_MODELS:
                    raise StkError("density model '%s' unknown; use one of %s"
                                   % (density_model, ", ".join(self.DENSITY_MODELS)))
                forces.Drag.AtmosphericDensityModel = self.find_enum(self.DENSITY_MODELS[key])
            if kp is not None and not 0.0 <= float(kp) <= 9.0:
                raise StkError("kp=%s is outside the Kp range 0-9 (an Ap value? Ap 15 "
                               "is about Kp 3)" % kp)
            if any(v is not None for v in (f107, f107_avg, kp)):
                forces.Drag.SetSolarFluxGeoMagType(self.find_enum("eSolarFluxGeoMagEnterManually"))
                flux = forces.Drag.SolarFluxGeoMag
                if f107 is not None:
                    flux.DailyF107 = float(f107)
                if f107_avg is not None:
                    flux.AverageF107 = float(f107_avg)
                if kp is not None:
                    flux.GeomagneticIndex = float(kp)
        forces.SolarRadiationPressure.Use = bool(srp)
        if srp:
            srp_model = forces.SolarRadiationPressure.SRPModel.Model
            try:
                srp_model.Cr = float(cr)
                if srp_area_mass is not None or drag_area_mass is not None:
                    srp_model.AreaMassRatio = float(srp_area_mass if srp_area_mass is not None
                                                    else drag_area_mass)
            except Exception as exc:
                raise StkError("could not set SRP Cr/area-mass: %s" % self._com_reason(exc))
        if third_bodies is not None:
            bodies = forces.ThirdBodyGravity
            bodies.RemoveAll()
            for body in third_bodies:
                bodies.AddThirdBody(body)
        if gravity_degree is not None:
            gravity = forces.CentralBodyGravity
            try:
                gravity.MaxDegree = int(gravity_degree)
                gravity.MaxOrder = int(gravity_degree)
            except Exception as exc:
                # MEASURED (smoke test): with EGM2008 and STK's default force
                # model, degree/order must be 21-100 -- 4 was refused.
                raise StkError("gravity degree %s refused for %s: %s. Leave "
                               "gravity_degree unset to keep STK's default (21)."
                               % (gravity_degree, gravity.File, self._com_reason(exc)))

    LIFETIME_DENSITY_MODELS = ("1976 Standard", "CIRA 1972", "Harris-Priester",
                               "Jacchia 1970", "Jacchia 1971", "Jacchia-Roberts",
                               "MSIS 1986", "MSISE 1990", "NRLMSISE 2000",
                               "Jacchia70Lifetime")

    def satellite_lifetime(self, sat_path, spacecraft=None, mass_kg=None, dims_m=None,
                           area_m2=None, cd=2.2, cr=1.3, density_model=None,
                           max_years=100.0):
        """Orbital lifetime from STK's Lifetime tool. Returns a dict.

        {"decays": bool, "decay_date": UTCG or None, "lifetime_days": float or
         None, "orbits": int or None, "assumption": str, "message": STK's text}

        STK 11 has no object-model interface for this: it is the Connect pair
        SetLifetime / Lifetime. MEASURED (bridge job 0087): 3U at 400 km ->
        223 days; double mass -> x2.13 (mass is kg); 500 km -> 7.2 years;
        NRLMSISE-00 within 2% of the default Jacchia70Lifetime. Each run takes
        well under a second. STK rounds long lifetimes ("1.3 years" = +-18
        days), so the lifetime is computed here from the exact decay date.
        Areas are absolute m^2 (tumbling average from the spacecraft profile).
        """
        import re
        mass, area, described = self.spacecraft_mass_area(spacecraft, mass_kg, dims_m, area_m2)
        settings = [("DragCoeff", cd), ("ReflectCoeff", cr), ("DragArea", area),
                    ("SunArea", area), ("Mass", mass), ("LimitType", "Duration"),
                    ("DurationLimit", float(max_years) * 365.25)]
        if density_model:
            match = next((m for m in self.LIFETIME_DENSITY_MODELS
                          if m.lower() == str(density_model).lower()), None)
            if match is None:
                raise StkError("lifetime density model '%s' unknown; use one of %s"
                               % (density_model, ", ".join(self.LIFETIME_DENSITY_MODELS)))
            settings.append(("DensityModel", '"%s"' % match))
        for option, value in settings:
            self.cmd("SetLifetime %s %s %s" % (sat_path, option, value),
                     label="SetLifetime %s" % option)
        try:
            reply = self.root.ExecuteCommand("Lifetime %s" % sat_path)
            text = " ".join(str(reply.Item(i)) for i in range(reply.Count))
        except pywintypes.com_error as exc:
            raise StkError("Lifetime failed for %s: %s" % (sat_path, self._com_reason(exc)))
        result = {"decays": False, "decay_date": None, "lifetime_days": None,
                  "orbits": None, "assumption": described, "message": text.strip()}
        date = re.search(r"estimated to be on (.+?) after (\d+) orbits", text)
        if date:
            decay = date.group(1).strip()
            convert = self.root.ConversionUtility.ConvertDate
            epoch = self._start or str(self.root.CurrentScenario.StartTime)
            days = (float(convert("UTCG", "EpSec", decay)) -
                    float(convert("UTCG", "EpSec", epoch))) / 86400.0
            result.update(decays=True, decay_date=decay, lifetime_days=days,
                          orbits=int(date.group(2)))
        sys.stderr.write("[assume] lifetime of %s -- %s, Cd %.2f, Cr %.2f\n"
                         % (sat_path, described, cd, cr))
        return result

    def mean_elements(self, sat_path, theory="Brouwer-Lyd Mean Short", frame="TrueOfDate",
                      step_sec=3600.0, start=None, stop=None):
        """MEAN orbital elements over time -- the right way to measure decay.

        Osculating elements swing by kilometres every orbit at low altitude:
        sampling them once a day showed -3.7 km of "decay" over 7 days with
        drag OFF (bridge job 0082). Mean elements remove the short-period
        terms. ``theory``: "Brouwer-Lyd Mean Short", "Brouwer-Lyd Mean Long",
        "Kozai-Izsak Mean".

        MEASURED (Pronoia runs): STK REFUSES mean elements for a satellite on
        the J2Perturbation propagator. J2 there is already secular-only (no
        short-period terms), so read "Classical Elements" instead -- they are
        effectively mean. Use this on HPOP / SGP4 / Astrogator satellites.
        """
        return self.read_data(sat_path, theory, group=frame, step_sec=step_sec,
                              start=start, stop=stop)

    def set_hpop_gravity(self, sat_path, grv_file, max_degree, max_order):
        """Configure the HPOP central-body gravity field.

        Only valid on a satellite whose propagator is HPOP. All properties
        here are strings/numbers -- no enums involved.

        Watch the valid degree/order range of the gravity file: the lunar
        ``GL0420A.grv`` accepts 48 to 420, and STK raises a com_error outside
        that range. Higher degree = slower propagation.
        """
        # Resolve a bare filename against STK's own data folders. The caller
        # (often a generated script) has no way to know the install path.
        resolved = self.find_data_file(str(grv_file))
        if resolved is None:
            searched = getattr(self, "_last_search", [])
            raise StkError(
                "Gravity file '%s' was not found in this STK installation.\n"
                "  STKHome reported as: %s\n"
                "  Looked in:\n%s\n"
                "  Pass an absolute path to set_hpop_gravity, or use a "
                "propagator that needs no gravity file (J2Perturbation)."
                % (grv_file, self.directory("STKHome") or "unknown",
                   "\n".join("    %s" % s for s in searched[:8]) or "    (no STK root found)"))

        try:
            gravity = self.obj(sat_path).Propagator.ForceModel.CentralBodyGravity
            gravity.File = resolved
            gravity.MaxDegree = int(max_degree)
            gravity.MaxOrder = int(max_order)
        except pywintypes.com_error as exc:
            raise StkError(
                "Failed to set HPOP gravity on %s (file=%s degree=%s order=%s). "
                "Check that the propagator is HPOP, the .grv path exists, and the "
                "degree/order are inside the file's valid range: %s"
                % (sat_path, grv_file, max_degree, max_order, exc))
        self.propagate(sat_path)

    def propagate(self, sat_path):
        """Re-run the propagator. Needed after changing the force model."""
        try:
            self.obj(sat_path).Propagator.Propagate()
        except pywintypes.com_error as exc:
            raise StkError("Propagation failed for %s: %s" % (sat_path, exc))

    def set_epoch(self, sat_path, epoch):
        """Re-anchor an existing orbit to a new epoch, keeping its shape."""
        self.cmd('SetState %s Epoch "%s"' % (sat_path, epoch),
                 label="re-epoch %s" % sat_path, fatal=False)
        self.propagate(sat_path)

    # -- sensors (visual FOV cones) -----------------------------------------
    def _new_sensor(self, parent_path, name, who):
        self._require_path(parent_path, who)
        path = self.unique_path("%s/Sensor/%s" % (parent_path, name))
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / %s/Sensor %s" % (parent_path, actual), label="create sensor")
        return path

    @_accept_aliases(angular_res_deg="angular_resolution_deg",
                     sat="parent_path", sat_path="parent_path")
    def add_sensor_conic(self, parent_path, name, half_angle_deg,
                         angular_resolution_deg=0.1):
        """Add a simple conic sensor -- a visible FOV cone in the 3D window.

        Uses the object-model ``CommonTasks.SetPatternSimpleConic`` (which
        takes plain numbers, no enums). The Connect ``Define ... SimpleConic``
        form is kept as a fallback because it was REJECTED on STK 11 in
        testing even though ``Define ... Rectangular`` was accepted.

        Keep this conceptually separate from the transmitter/receiver RF
        chain: a sensor draws geometry, a transmitter carries a link budget.
        An object can have both.
        """
        path = self._new_sensor(parent_path, name, "add_sensor_conic")
        try:
            self.obj(path).CommonTasks.SetPatternSimpleConic(
                float(half_angle_deg), float(angular_resolution_deg))
            return path
        except Exception as exc:
            first = self._com_reason(exc) if isinstance(exc, pywintypes.com_error) else exc
        if not self.cmd("Define %s SimpleConic %.6f %.6f"
                        % (path, float(half_angle_deg), float(angular_resolution_deg)),
                        label="conic sensor pattern", fatal=False):
            raise StkError("Could not set a conic pattern on %s. Object model "
                           "route said: %s" % (path, first))
        return path

    @_accept_aliases(half_across_deg="half_angle_across_deg",
                     half_along_deg="half_angle_along_deg",
                     sat="parent_path", sat_path="parent_path")
    def add_sensor_rectangular(self, parent_path, name,
                               half_angle_across_deg, half_angle_along_deg):
        """Add a rectangular sensor. Both arguments are HALF-angles.

        For a camera quoted as a full FOV (e.g. 2.22 deg across-track by
        1.66 deg along-track), pass half of each: 1.11 and 0.83.
        """
        path = self._new_sensor(parent_path, name, "add_sensor_rectangular")
        try:
            self.obj(path).CommonTasks.SetPatternRectangular(
                float(half_angle_across_deg), float(half_angle_along_deg))
            return path
        except Exception as exc:
            first = self._com_reason(exc) if isinstance(exc, pywintypes.com_error) else exc
        if not self.cmd("Define %s Rectangular %.6f %.6f"
                        % (path, float(half_angle_across_deg), float(half_angle_along_deg)),
                        label="rectangular sensor pattern", fatal=False):
            raise StkError("Could not set a rectangular pattern on %s. Object "
                           "model route said: %s" % (path, first))
        return path

    #: Body-frame elevation that points a sensor at the ground.
    #:
    #: MEASURED, not assumed: probe_pointing_reports.py builds a 45 deg cone,
    #: points it each way, and computes SENSOR-to-facility access. At -90 the
    #: sensor saw nothing (staring at space); at +90 it saw the ground. This
    #: was wrong in every scenario built before it was measured, and no test
    #: caught it because all access checks were satellite-to-facility, which
    #: is pointing-independent.
    NADIR_ELEVATION_DEG = 90.0

    def point_nadir(self, sensor_path):
        """Point a sensor along body +Z -- the ground ONLY if the bus is nadir-pointed.

        MEASURED (job 0125): under "Sun Alignment With Nadir Constraint" body +X
        sat on the Sun within 0.001 deg and +Z stayed only roughly nadir (84 s
        vs 88 s access on a nadir bus). With any other attitude this "nadir"
        camera looks wherever +Z points; use point_at_target for a camera that
        must see a site regardless of bus attitude.

        Verify with :meth:`compute_access` from the SENSOR (not its parent
        satellite) to a facility: a satellite-to-facility access ignores
        sensor pointing entirely and will look identical whichever way the
        sensor faces.
        """
        self.cmd("Point %s Fixed AzEl 0.0 %.1f Rotate"
                 % (sensor_path, self.NADIR_ELEVATION_DEG),
                 label="nadir pointing", fatal=False)

    def point_az_el(self, sensor_path, az_deg, el_deg, about_boresight="Rotate"):
        """Fixed az/el pointing. ``about_boresight`` is "Rotate" or "Hold"."""
        self.cmd("Point %s Fixed AzEl %.6f %.6f %s"
                 % (sensor_path, float(az_deg), float(el_deg), about_boresight),
                 label="az/el pointing", fatal=False)

    def point_at_target(self, sensor_path, target_path):
        """Slew a sensor to track another object.

        Connect's ``Point ... Targeted Track`` form was REJECTED on STK 11,
        so the object-model route is primary: set the pointing type to
        targeted (enum resolved from this install's type library) and add the
        target. Connect forms are kept as fallbacks.

        Raises :class:`StkError` if nothing works -- silently leaving a sensor
        unpointed produces zero access intervals later, which reads as a
        physics result rather than a failure.
        """
        attempts = []
        pointing_enum = self.find_enum("eSnPtTargeted", "snpt", "targeted")
        if pointing_enum is not None:
            try:
                sensor = self.obj(sensor_path)
                sensor.SetPointingType(pointing_enum)
                targets = sensor.Pointing.Targets
                # Targets.Add wants a class-qualified path; accepted spelling
                # varies, so try the common ones.
                for candidate in (target_path.lstrip("*/"), target_path,
                                  target_path.replace("*/", "")):
                    try:
                        targets.Add(candidate)
                        return True
                    except Exception as exc:
                        attempts.append("    [Targets.Add %r] %s" % (candidate, exc))
            except Exception as exc:
                attempts.append("    [SetPointingType] %s" % exc)
        else:
            attempts.append("    [object model] no targeted-pointing enum in this type library")

        for template in ("Point %s Targeted Track %s",
                         "Point %s Targeted %s",
                         "Point %s Targeted AddObject %s"):
            command = template % (sensor_path, target_path)
            try:
                self.root.ExecuteCommand(command)
                return True
            except pywintypes.com_error as exc:
                attempts.append("    [%s] %s" % (command.split()[2], self._com_reason(exc)))

        raise StkError(
            "Could not point %s at %s -- every route was rejected.\n"
            "  Attempts:\n%s\n"
            "  An unpointed sensor reports zero access, which looks like a "
            "physics result rather than a failure, so this raises instead."
            % (sensor_path, target_path, "\n".join(attempts)))

    # -- RF chain -----------------------------------------------------------
    @staticmethod
    def _try_set_antenna(model, antenna_model, path):
        """Select an embedded antenna pattern, if this model has an antenna.

        The SIMPLE transmitter/receiver models have no ``AntennaControl`` at
        all -- they are characterised by EIRP alone, which is exactly why
        they are simple. Asking for a pattern there is not an error, it is a
        no-op, so this reports and moves on rather than failing the helper.

        Where AntennaControl does exist, note that pattern selection is a
        METHOD -- ``SetEmbeddedModel(name)`` -- not an ``EmbeddedModel = name``
        assignment.
        """
        try:
            control = model.AntennaControl
        except Exception:
            sys.stderr.write(
                "[info] %s uses a Simple model, which has no antenna object; "
                "the '%s' pattern was not applied. Use a Complex model if you "
                "need a real antenna pattern.\n" % (path, antenna_model))
            return False
        try:
            control.SetEmbeddedModel(antenna_model)
            return True
        except Exception as exc:
            sys.stderr.write("[warn] could not select antenna model '%s' on %s: %s\n"
                             % (antenna_model, path, exc))
            return False

    @_accept_aliases(sat="parent_path", sat_path="parent_path",
                     gs="parent_path", facility="parent_path")
    def add_transmitter(self, parent_path, name, freq_ghz, eirp_dbw,
                        data_rate_kbps=None, antenna_model=None):
        """Mount a Simple Transmitter Model on a satellite or facility.

        Two STK gotchas are handled here so callers cannot get them wrong:

        * the EIRP property is ``.Eirp`` (camelCase) -- ``.EIRP`` raises
        * antenna pattern selection is a METHOD, ``SetEmbeddedModel(name)``,
          NOT an ``EmbeddedModel = name`` assignment

        ``antenna_model`` is a built-in embedded model name: "Isotropic",
        "Gaussian", "Parabolic", "External Antenna Pattern".
        """
        self._require_path(parent_path, "add_transmitter")
        path = self.unique_path("%s/Transmitter/%s" % (parent_path, name))
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / %s/Transmitter %s" % (parent_path, actual),
                 label="create transmitter")
        try:
            transmitter = self.obj(path)
            transmitter.SetModel("Simple Transmitter Model")
            model = transmitter.Model
            model.Frequency = float(freq_ghz)   # GHz
            model.Eirp = float(eirp_dbw)        # dBW -- '.Eirp', not '.EIRP'
            if data_rate_kbps is not None:
                try:
                    model.DataRate = float(data_rate_kbps)   # kbps
                except Exception:
                    sys.stderr.write("[warn] data rate not settable on this "
                                     "transmitter model; skipped.\n")
            if antenna_model:
                self._try_set_antenna(model, antenna_model, path)
        except pywintypes.com_error as exc:
            raise StkError("Failed to configure transmitter %s: %s" % (path, exc))
        return path

    @_accept_aliases(sat="parent_path", sat_path="parent_path",
                     gs="parent_path", facility="parent_path")
    def add_receiver(self, parent_path, name, freq_ghz=None,
                     antenna_model=None, auto_select_frequency=True):
        """Mount a Simple Receiver Model on a satellite or facility.

        NOTE ON FREQUENCY (verified against STK 11 by discover_stk_api.py):
        a receiver's ``Frequency`` is READ-ONLY and there is no
        ``AutoSelectFrequency`` property on either the Simple or Complex
        receiver model; the Connect ``Define ... Frequency`` forms are
        rejected too. STK derives the receive frequency from the transmitter
        on the other end of the link, which is the correct behaviour -- you
        set the frequency on the TRANSMITTER and the receiver follows.

        ``freq_ghz`` is therefore accepted for symmetry with
        :meth:`add_transmitter` and for forward compatibility, but is only
        applied if this build actually allows it. It is never an error.
        """
        self._require_path(parent_path, "add_receiver")
        path = self.unique_path("%s/Receiver/%s" % (parent_path, name))
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / %s/Receiver %s" % (parent_path, actual), label="create receiver")
        try:
            receiver = self.obj(path)
            receiver.SetModel("Simple Receiver Model")
            model = receiver.Model
            if freq_ghz is not None and not auto_select_frequency:
                try:
                    model.AutoSelectFrequency = False
                    model.Frequency = float(freq_ghz)
                except Exception:
                    sys.stderr.write(
                        "[info] receiver frequency is read-only on this STK build; "
                        "STK will derive it from the linked transmitter.\n")
            if antenna_model:
                self._try_set_antenna(model, antenna_model, path)
        except pywintypes.com_error as exc:
            raise StkError("Failed to configure receiver %s: %s" % (path, exc))
        return path

    # -- communications: real radios, real antennas, real link budgets -----
    #
    # MEASURED on STK 11 by bridge jobs 0046-0057. Four traps, every one of
    # which STK accepts silently and turns into a plausible link budget:
    #
    #   1. An antenna EMBEDDED in a transmitter/receiver has a read-only
    #      DesignFrequency stuck at 14.5 GHz, and STK scales the pattern from
    #      it to the operating frequency: a "14 dBi" UHF Yagi delivered
    #      -16.4 dBi at 437 MHz. A standalone Antenna object accepts it.
    #   2. The Gaussian model defaults to DIAMETER input (a 1 m dish); the
    #      gain is read-only until InputType is switched to gain.
    #   3. A dipole's axis lies on its boresight, so nadir-pointed it puts its
    #      null on the ground (-10 dBi).
    #   4. Noise defaults to a flat 290 K with no sky/sun/earth, the link
    #      margin reads -9999.99 until a threshold is set, and rain and
    #      atmospheric losses are OFF at scenario level.
    #
    # The link budget is not on the transmitter or receiver at all: it is the
    # "Link Information" provider of the ACCESS between them.
    #
    # A ground antenna that follows the satellite is built as: a sensor on
    # the facility tracking the target, a standalone Antenna on that sensor,
    # and the receiver on the FACILITY linked to it. A receiver on the sensor
    # itself cannot link (measured, job 0057). Link order matters: set
    # ReferenceType to link FIRST, then LinkedAntennaObject.

    def set_propagation_losses(self, rain=True, atmosphere=True,
                               rain_model="ITU-R P618-12",
                               atmosphere_model="ITU-R P676-9"):
        """Turn on rain and atmospheric absorption for every link in the scenario.

        Both are OFF by default in STK, so a Ka- or Ku-band budget without
        this call is optimistic by however many dB of rain fade apply.
        At UHF the effect is ~0.02 dB, which is correct, not a failure.
        """
        channel = self.root.CurrentScenario.RFEnvironment.PropagationChannel
        channel.EnableRainLoss = bool(rain)
        if rain:
            channel.SetRainLossModel(rain_model)
        channel.EnableAtmosAbsorption = bool(atmosphere)
        if atmosphere:
            channel.SetAtmosAbsorptionModel(atmosphere_model)
        return {"rain": rain_model if rain else None,
                "atmosphere": atmosphere_model if atmosphere else None}

    def add_antenna(self, parent_path, name, freq_ghz, model="Gaussian",
                    gain_dbi=None, beamwidth_deg=None, diameter_m=None,
                    efficiency_pct=None):
        """A standalone antenna whose pattern is defined AT the operating frequency.

        Use this for anything with gain -- a ground Yagi, a dish, a patch --
        and link a radio to it. ``model`` is one of STK's 48 antenna models
        ("Gaussian", "Parabolic", "Helix", "Dipole", "Hemispherical",
        "Isotropic", "Square Horn", ...). For Gaussian/Parabolic give ONE of
        ``gain_dbi``, ``beamwidth_deg`` or ``diameter_m``; the others follow.

        STK 11 has no Yagi model: a Gaussian with the Yagi's gain is the
        standard stand-in (14 dBi gives a 35.9 deg beam).
        """
        self._require_path(parent_path, "add_antenna")
        path = self.unique_path("%s/Antenna/%s" % (parent_path, name))
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / %s/Antenna %s" % (parent_path, actual), label="create antenna")
        antenna = self.obj(path)
        try:
            antenna.SetModel(model)
        except Exception as exc:
            raise StkError("'%s' is not an antenna model here. Models: %s"
                           % (model, ", ".join(str(m) for m in antenna.SupportedModels)))
        m = antenna.Model
        chosen = [k for k, v in (("gain", gain_dbi), ("beamwidth", beamwidth_deg),
                                 ("diameter", diameter_m)) if v is not None]
        if len(chosen) > 1:
            raise StkError("give only one of gain_dbi, beamwidth_deg, diameter_m "
                           "(got %s) -- the others follow from it" % ", ".join(chosen))
        if chosen and hasattr(m, "InputType"):
            m.InputType = self.find_enum({
                "gain": "eAntennaModelInputTypeMainlobeGain",
                "beamwidth": "eAntennaModelInputTypeBeamwidth",
                "diameter": "eAntennaModelInputTypeDiameter"}[chosen[0]])
        # design frequency BEFORE the size parameter, so the pattern is sized
        # for the band it will actually be used in
        try:
            m.DesignFrequency = float(freq_ghz)
        except Exception:
            pass
        for attr, value in (("MainlobeGain", gain_dbi), ("Beamwidth", beamwidth_deg),
                            ("Diameter", diameter_m), ("Efficiency", efficiency_pct)):
            if value is not None:
                try:
                    setattr(m, attr, float(value))
                except Exception as exc:
                    raise StkError("could not set %s=%s on a %s antenna: %s"
                                   % (attr, value, model, self._com_reason(exc)))
        design = getattr(m, "DesignFrequency", None)
        if design is not None and abs(float(design) - float(freq_ghz)) > 1e-6:
            sys.stderr.write(
                "[warn] %s design frequency is %.4f GHz, not %.4f GHz -- its gain "
                "will be scaled between the two.\n" % (path, float(design), float(freq_ghz)))
        return path

    def _link_radio_to_antenna(self, radio_path, antenna_path):
        control = self.obj(radio_path).Model.AntennaControl
        options = [str(o) for o in control.SupportedLinkedAntennaObjects]
        tail = antenna_path.split("*/", 1)[-1]
        target = next((o for o in options if o == tail or tail.endswith(o) or o.endswith(tail)), None)
        if target is None:
            raise StkError(
                "%s cannot link to %s. Linkable antennas: %s.\n"
                "  A receiver mounted on a sensor cannot link at all -- put it on "
                "the facility (or vehicle) and link to the antenna on the sensor."
                % (radio_path, antenna_path, ", ".join(options) or "none"))
        control.ReferenceType = self.find_enum("eAntennaControlRefTypeLink")   # first
        control.LinkedAntennaObject = target                                   # then
        return target

    _POLARIZATIONS = {"rhc": "ePolarizationTypeRHC", "lhc": "ePolarizationTypeLHC",
                      "linear": "ePolarizationTypeLinear",
                      "horizontal": "ePolarizationTypeHorizontal",
                      "vertical": "ePolarizationTypeVertical",
                      "elliptical": "ePolarizationTypeElliptical"}

    def _set_polarization(self, model, polarization):
        """RHC / LHC / linear / horizontal / vertical / elliptical, or None.

        MEASURED (bridge job 0063), transmitter RHC: RHC receiver 0 dB,
        linear -3.01 dB, LHC -60 dB (the link dies). Polarization is OFF
        unless set, i.e. no polarization loss is modelled at all.

        MEASURED (bridge job 0068): enabling polarization on a receiver ALSO
        lowers its computed noise temperature -- 126.3 K -> 76.8 K at the same
        instant near the horizon -- because STK applies polarization to the
        Earth/atmosphere/sky noise too. The net margin change is therefore
        -0.85 dB at the horizon, not -3.01. Standard radiometry already
        assumes one polarization in antenna noise temperature, so this may
        make low-elevation margins up to ~2 dB optimistic. Unconfirmed
        against AGI's documentation -- state it when reporting a budget.
        """
        if polarization is None:
            return
        key = str(polarization).strip().lower()
        if key not in self._POLARIZATIONS:
            raise StkError("polarization '%s' unknown; use one of %s"
                           % (polarization, ", ".join(self._POLARIZATIONS)))
        model.EnablePolarization = True
        model.SetPolarizationType(self.find_enum(self._POLARIZATIONS[key]))
        if hasattr(model, "SystemNoiseTemperature"):
            sys.stderr.write(
                "[note] polarization on a receiver also lowers STK's computed noise "
                "temperature (126 K -> 77 K measured near the horizon); low-elevation "
                "margins may be up to ~2 dB optimistic. Unconfirmed STK behaviour.\n")

    def add_radio_transmitter(self, parent_path, name, freq_ghz, power_dbw,
                              data_rate_kbps, modulation="BPSK",
                              antenna=None, antenna_model="Isotropic",
                              polarization=None):
        """A transmitter defined by POWER and a real antenna (STK's complex model).

        ``antenna`` is the path of an antenna from :meth:`add_antenna` to link
        to. Without it an embedded ``antenna_model`` is used -- fine for
        frequency-independent patterns ("Isotropic", "Hemispherical"), but
        do not embed a gain antenna: its design frequency is stuck at 14.5 GHz.
        ``modulation`` is one of STK's 38 (BPSK, QPSK, 8PSK, OQPSK, QAM16...).
        """
        self._require_path(parent_path, "add_radio_transmitter")
        path = self.unique_path("%s/Transmitter/%s" % (parent_path, name))
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / %s/Transmitter %s" % (parent_path, actual), label="create transmitter")
        tx = self.obj(path)
        tx.SetModel("Complex Transmitter Model")
        m = tx.Model
        m.Frequency = float(freq_ghz)
        m.Power = float(power_dbw)
        m.DataRate = float(data_rate_kbps) / 1000.0          # Mb/s
        try:
            m.SetModulator(modulation)
        except Exception:
            raise StkError("'%s' is not a modulation here. Options: %s"
                           % (modulation, ", ".join(str(x) for x in m.SupportedModulators)))
        self._set_polarization(m, polarization)
        if antenna:
            self._link_radio_to_antenna(path, antenna)
        else:
            if antenna_model.lower() not in ("isotropic", "hemispherical"):
                sys.stderr.write(
                    "[warn] embedding a '%s' antenna in %s: embedded antennas keep a "
                    "14.5 GHz design frequency, so its gain will be mis-scaled. Use "
                    "add_antenna() and pass antenna=.\n" % (antenna_model, path))
            m.AntennaControl.SetEmbeddedModel(antenna_model)
        return path

    def add_radio_receiver(self, parent_path, name, antenna=None,
                           antenna_model="Isotropic", lna_noise_figure_db=None,
                           noise_temperature_k=None, required_ebno_db=None,
                           rain=True, rain_outage_percent=0.1,
                           polarization=None):
        """A receiver with a real antenna, computed noise and a link margin.

        Noise is CALCULATED (sun, sky, cosmic background, atmosphere, rain,
        plus the LNA) unless ``noise_temperature_k`` forces a constant --
        STK's default is a flat 290 K. ``required_ebno_db`` enables the link
        margin (e.g. 9.6 dB for BPSK at a BER of 1e-5); without it STK
        reports the margin as -9999.99. The demodulator auto-matches the
        transmitter's modulation.
        """
        self._require_path(parent_path, "add_radio_receiver")
        if "/Sensor/" in parent_path and antenna:
            raise StkError(
                "a receiver on a sensor cannot link to an antenna (measured on STK "
                "11). Put the receiver on %s and link to the antenna on the sensor."
                % parent_path.split("/Sensor/")[0])
        path = self.unique_path("%s/Receiver/%s" % (parent_path, name))
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / %s/Receiver %s" % (parent_path, actual), label="create receiver")
        rx = self.obj(path)
        rx.SetModel("Complex Receiver Model")
        m = rx.Model
        if antenna:
            self._link_radio_to_antenna(path, antenna)
        else:
            m.AntennaControl.SetEmbeddedModel(antenna_model)
        noise = m.SystemNoiseTemperature
        if noise_temperature_k is not None:
            noise.ComputeType = self.find_enum("eNoiseTempComputeTypeConstant")
            noise.ConstantNoiseTemperature = float(noise_temperature_k)
        else:
            noise.ComputeType = self.find_enum("eNoiseTempComputeTypeCalculate")
            if lna_noise_figure_db is not None:
                noise.LnaNoiseFigure = float(lna_noise_figure_db)
            sky = noise.AntennaNoiseTemperature
            sky.ComputeType = self.find_enum("eNoiseTempComputeTypeCalculate")
            for flag in ("UseSun", "UseEarth", "UseCosmicBackground", "UseAtmosphere", "UseRain"):
                try:
                    setattr(sky, flag, True)
                except Exception:
                    pass          # e.g. UseEarth is read-only on some mounts
        self._set_polarization(m, polarization)
        if rain:
            # MEASURED (bridge job 0060): UseRain and RainOutagePercent are
            # READ-ONLY while rain loss is off at scenario level -- which is
            # STK's default. Turn the scenario losses on rather than fail or
            # silently build a rain-free budget.
            channel = self.root.CurrentScenario.RFEnvironment.PropagationChannel
            if not channel.EnableRainLoss:
                self.set_propagation_losses()
                sys.stderr.write(
                    "[info] rain loss was off at scenario level; enabled ITU-R "
                    "P618-12 rain and P676-9 atmospheric absorption for all links "
                    "(pass rain=False to skip).\n")
            m.UseRain = True
            m.RainOutagePercent = float(rain_outage_percent)
        if required_ebno_db is not None:
            margin = m.LinkMargin
            margin.Enable = True
            margin.Type = self.find_enum("eLinkMarginTypeEbOverNo")
            margin.Threshold = float(required_ebno_db)
        return path

    def add_tracking_ground_antenna(self, facility_path, target_path, name,
                                    freq_ghz, gain_dbi, model="Gaussian"):
        """A ground antenna that follows ``target_path`` through every pass.

        Builds the only layout that works on STK 11: a sensor on the facility
        tracking the target, with the antenna on that sensor. Link a receiver
        on the FACILITY to the returned antenna path.
        """
        mount = self.add_sensor_conic(facility_path, name + "_Mount", 5.0)
        self.point_at_target(mount, target_path)
        return self.add_antenna(mount, name, freq_ghz, model=model, gain_dbi=gain_dbi)

    #: NOTE "Polarization Effic" is a LOSS IN dB (0.0 = no loss, -3.01 = circular
    #: into linear, -60 = opposite-hand circular), not a 0-1 efficiency.
    #: Measured in bridge job 0063.
    LINK_KEYS = ("Range", "Xmtr Gain", "EIRP", "Free Space Loss", "Atmos Loss",
                 "Rain Loss", "Prop Loss", "Rcvr Gain", "Rcvr Elevation",
                 "Carrier Power at Rcvr Input", "Tequivalent", "g/T", "C/No",
                 "C/N", "Eb/No", "BER", "Link Margin", "Freq. Doppler Shift")

    def link_budget(self, tx_path, rx_path, elements=None, step_sec=10.0,
                    start=None, stop=None):
        """The link budget between a transmitter and a receiver, over time.

        Returns a DataResult of STK's "Link Information" (61 elements, e.g.
        EIRP, Free Space Loss, Rcvr Gain, g/T, C/No, Eb/No, BER, Link
        Margin). It lives on the ACCESS between the two radios -- the
        transmitter and receiver have no link-budget provider of their own.
        """
        tx, rx = self.obj(tx_path), self.obj(rx_path)
        access = tx.GetAccessToObject(rx)
        access.ComputeAccess()
        try:
            access._label = "%s -> %s" % (tx_path, rx_path)
        except Exception:
            pass
        return self.read_data(access, "Link Information",
                              elements=list(elements) if elements else ["Time"] + list(self.LINK_KEYS),
                              start=start, stop=stop, step_sec=step_sec)

    def link_passes(self, tx_path, rx_path, step_sec=10.0, min_margin_db=None):
        """One summary per pass: start, stop, peak Eb/No, worst margin, min range.

        With ``min_margin_db`` only the stretches of each pass where the link
        margin holds are returned -- "when does the link actually close?".

        This deliberately does NOT use STK's eCstrLinkMargin access
        constraint. MEASURED (bridge jobs 0061, 0064): with a tracking ground
        antenna that constraint made ComputeAccess run for 10+ minutes on a
        6-hour window and froze STK, while the same answer comes from the
        link budget's own samples in seconds.
        """
        data = self.link_budget(tx_path, rx_path, step_sec=step_sec,
                                elements=["Time", "Range", "Eb/No", "Link Margin"])
        times = data.column("Time")
        passes = []
        for start, stop, first, count in data.segments:
            if not count:
                continue
            idx = list(range(first, first + count))
            runs = [idx]
            if min_margin_db is not None:
                runs, current = [], []
                for i in idx:
                    if data.column("Link Margin")[i] >= float(min_margin_db):
                        current.append(i)
                    elif current:
                        runs.append(current)
                        current = []
                if current:
                    runs.append(current)
            for run in runs:
                ebno = [data.column("Eb/No")[i] for i in run]
                margin = [data.column("Link Margin")[i] for i in run]
                rng = [data.column("Range")[i] for i in run]
                passes.append({
                    "start": str(times[run[0]]) if min_margin_db is not None else start,
                    "stop": str(times[run[-1]]) if min_margin_db is not None else stop,
                    "samples": len(run), "peak_ebno_db": max(ebno),
                    "min_ebno_db": min(ebno), "worst_margin_db": min(margin),
                    "min_range_km": min(rng)})
        return passes

    # -- relays (bent pipe) and interference ---------------------------------
    #
    # MEASURED on STK 11 by bridge jobs 0069-0076.
    #
    # Bent pipe: a re-transmitter is a Transmitter on the satellite (it cannot
    # live under the receiver). Its frequency translation defaults to
    # f_out = f_in - 700 MHz (coefficients in Hz: [-7e8, 1]) -- a C-band 6/4 GHz
    # transponder. At any other band that gives a NEGATIVE downlink frequency;
    # the downlink leg then fills with sentinels (g/T 3000, Eb/No 3003 dB) and
    # the chain's "Eb/No Tot." silently equals the UPLINK alone -- a plausible
    # number that ignores half the link. The translation list cannot shrink
    # below two terms, so coefficients are replaced in place.
    # Once correct, STK combines the legs exactly: 1/tot = 1/up + 1/down,
    # verified to 0.0000 dB on every sample. The chain's total link margin
    # stays -9999.99 even with a threshold set; read the per-leg margins.
    #
    # Interference: a CommSystem takes CONSTELLATIONS of radios, not radios
    # ("cannot be added to the collection"). Its outputs need the victim
    # receiver as PreData. They compute on demand; the Connect
    # "Comm ... Compute" command is rejected and not needed.

    def add_bent_pipe(self, satellite_path, name, uplink_freq_ghz,
                      downlink_freq_ghz, output_power_dbw, antenna_model="Hemispherical",
                      antenna=None, lna_noise_figure_db=2.0):
        """A transparent (bent-pipe) transponder on a satellite.

        Returns ``(receiver_path, retransmitter_path)``. The re-transmitter
        runs at constant output power and translates ``uplink_freq_ghz`` to
        ``downlink_freq_ghz``. Chain it as [ground tx, receiver,
        retransmitter, ground rx] with :meth:`new_chain` and read the result
        with :meth:`chain_link_budget`.
        """
        shift_hz = (float(downlink_freq_ghz) - float(uplink_freq_ghz)) * 1e9
        if float(downlink_freq_ghz) <= 0:
            raise StkError("downlink frequency must be positive")
        rx = self.add_radio_receiver(satellite_path, name + "_Rx", antenna=antenna,
                                     antenna_model=antenna_model,
                                     lna_noise_figure_db=lna_noise_figure_db)
        path = self.unique_path("%s/Transmitter/%s" % (satellite_path, name + "_ReTx"))
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / %s/Transmitter %s" % (satellite_path, actual),
                 label="create re-transmitter")
        rt = self.obj(path)
        rt.SetModel("Complex Re-Transmitter Model")
        m = rt.Model
        m.OperationalMode = self.find_enum("eReTransmitterOpModeConstantOutputPower")
        m.SaturatedPower = float(output_power_dbw)
        poly = m.FrequencyTransferFunctionPolynomial
        poly.InsertAt(0, shift_hz)       # the list cannot drop below 2 terms,
        poly.RemoveAt(1)                 # so replace the offset in place
        coefficients = [poly.Item(i) for i in range(poly.Count)]
        if abs(coefficients[0] - shift_hz) > 1.0 or abs(coefficients[1] - 1.0) > 1e-9:
            raise StkError("re-transmitter translation read back as %r, expected [%g, 1]"
                           % (coefficients, shift_hz))
        if antenna:
            self._link_radio_to_antenna(path, antenna)
        else:
            m.AntennaControl.SetEmbeddedModel(antenna_model)
        return rx, path

    def chain_link_budget(self, chain_path, step_sec=60.0):
        """End-to-end link budget of a chain (e.g. a bent pipe). A DataResult.

        Elements carry a leg suffix: "Eb/No1" uplink, "Eb/No2" downlink, and
        "Eb/No Tot.2" the combined link. A downlink frequency <= 0 or the
        sentinel values (g/T 3000 dB) mean the translation is wrong -- raised
        here rather than returned as a plausible total.
        """
        data = self.read_data(chain_path, "Link Information", step_sec=step_sec)
        for key in [c for c in data.columns if c.startswith("Rcvd. Frequency")]:
            bad = [v for v in data.column(key) if isinstance(v, float) and v <= 0]
            if bad:
                raise StkError(
                    "%s: '%s' is %.4f GHz -- a negative/zero frequency. A "
                    "re-transmitter's translation is wrong (STK's default is "
                    "-700 MHz); the reported total would be the uplink alone."
                    % (chain_path, key, bad[0]))
        return data

    def _constellation_of(self, name, members):
        path = self.unique_path("*/Constellation/%s" % name)
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / */Constellation %s" % actual, label="create constellation")
        const = self.obj(path)
        for member in members:
            const.Objects.Add(self.clean_path(member).lstrip("*/"))
        return path

    def new_interference_study(self, name, wanted_transmitters, victim_receivers,
                               interferers, reference_bandwidth=None):
        """A CommSystem: wanted links plus interference sources.

        Each argument is a list of radio paths; they are wrapped into
        constellations, which is the only form a CommSystem accepts. Read the
        result with :meth:`interference_budget`.
        """
        path = self.unique_path("*/CommSystem/%s" % name)
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / */CommSystem %s" % actual, label="create comm system")
        cs = self.obj(path)
        for collection, members, tag in ((cs.Transmitters, wanted_transmitters, "Tx"),
                                         (cs.Receivers, victim_receivers, "Rx"),
                                         (cs.InterferenceSources, interferers, "Interf")):
            const = self._constellation_of("%s_%s" % (actual, tag), members)
            collection.Add(const.lstrip("*/"))
        cs.CalculateInterference = True
        if reference_bandwidth is not None:
            cs.ReferenceBandwidth = self.find_enum(
                "eCommSystemReferenceBandwidth" + str(reference_bandwidth))
        return path

    INTERFERENCE_KEYS = ("Range", "C/No", "C/N", "C/I", "C/(N+I)", "Eb/No",
                         "Eb/(No+I)", "BER", "BER+I", "J/S", "DeltaT/T", "IoverN")

    def interference_budget(self, study_path, victim_receiver, step_sec=60.0,
                            elements=None):
        """Wanted-link budget WITH interference at one victim receiver.

        A DataResult with C/N and C/(N+I), Eb/No and Eb/(No+I), C/I, BER+I,
        J/S, DeltaT/T, I/N ... ("Link Information" of the CommSystem, with the
        receiver as PreData). Rows before any interferer is visible show
        C/(N+I) == C/N, as they should, and C/I = 3000 -- a SENTINEL for "no
        interferer in view", not a ratio; never average it.
        Measured consistency (job 0077): with interference dominant,
        Eb/(No+I) = C/I + 10log10(bandwidth/bit rate) (3.01 dB for BPSK).
        """
        return self.read_data(study_path, "Link Information", step_sec=step_sec,
                              pre_data=victim_receiver,
                              elements=(["Time"] + list(elements)) if elements else None)

    # -- operations MEASURED to be expensive on this STK ---------------------
    #
    # A timeout on the caller's side does NOT stop STK: it keeps computing the
    # call it was given, and every later request blocks behind it. So an
    # expensive call must be announced with its cost -- or replaced -- BEFORE
    # it is made. Each entry: what it is, what was measured, what to do.

    EXPENSIVE_OPERATIONS = {
        "link_constraint": {
            "what": "an RF access constraint (link margin, EIRP, BER+I, flux "
                    "density, received power) on a receiver or transmitter",
            "measured": "STK 11, bridge jobs 0061/0064: ComputeAccess did not "
                        "finish in 10+ minutes on a 6-hour window with a "
                        "tracking ground antenna, and STK stayed frozen until "
                        "force-closed",
            "instead": "session.link_passes(tx, rx, min_margin_db=...) -- the "
                       "same answer from the link budget's own samples, in "
                       "seconds",
        },
    }

    def cost_note(self, operation):
        """The measured cost and the cheaper alternative, as one message."""
        entry = self.EXPENSIVE_OPERATIONS[operation]
        return ("EXPENSIVE: %s.\n  Measured: %s.\n  Instead: %s."
                % (entry["what"], entry["measured"], entry["instead"]))

    _LINK_CONSTRAINTS = {
        "margin": "eCstrLinkMargin", "eirp": "eCstrLinkEIRP",
        "ber+i": "eCstrBERPlusI", "flux": "eCstrFluxDensity",
        "power": "eCstrPowerAtReceiverInput",
        "isotropic_power": "eCstrRcvdIsotropicPower",
    }

    def add_link_constraint(self, radio_path, kind="margin", minimum=None,
                            maximum=None, allow_slow=False):
        """An RF access constraint -- refused unless ``allow_slow=True``.

        See EXPENSIVE_OPERATIONS["link_constraint"]: this can freeze STK for
        many minutes. For "when does the link close?" use
        link_passes(min_margin_db=...), which is fast.
        """
        if not allow_slow:
            raise StkError(self.cost_note("link_constraint") +
                           "\n  Pass allow_slow=True only after warning the user.")
        key = str(kind).lower()
        if key not in self._LINK_CONSTRAINTS:
            raise StkError("link constraint '%s' unknown; use one of %s"
                           % (kind, ", ".join(self._LINK_CONSTRAINTS)))
        sys.stderr.write("[warn] " + self.cost_note("link_constraint") + "\n")
        constraint = self.obj(radio_path).AccessConstraints.AddConstraint(
            self.find_enum(self._LINK_CONSTRAINTS[key]))
        if minimum is not None:
            constraint.EnableMin, constraint.Min = True, float(minimum)
        if maximum is not None:
            constraint.EnableMax, constraint.Max = True, float(maximum)
        return constraint

    # -- constraints (verified accepted by probe_capabilities.py) -----------
    #: Constraint names this STK accepted. LightingSunElevAngle was rejected.
    KNOWN_CONSTRAINTS = ("ElevationAngle", "Range", "Altitude",
                         "SunElevationAngle", "Lighting",
                         "GroundElevAngle", "AngleOffBoresight")

    #: Constraint names that mean something this STK spells differently.
    #: Off-nadir is the supplement of elevation, so a MAXIMUM off-nadir angle
    #: becomes a MINIMUM elevation angle -- the bound swaps sides.
    _CONSTRAINT_ALIASES = {
        "offnadirangle": "ElevationAngle",
        "offnadir": "ElevationAngle",
        "nadirangle": "ElevationAngle",
        "grazingangle": "ElevationAngle",
        "elevation": "ElevationAngle",
        "elevationangle": "ElevationAngle",
        "range": "Range",
        "altitude": "Altitude",
        "lighting": "Lighting",
        "sunelevationangle": "SunElevationAngle",
        "sunelevation": "SunElevationAngle",
    }

    #: Aliases measured from the local vertical rather than the horizon.
    _SUPPLEMENTARY_ALIASES = ("offnadirangle", "offnadir", "nadirangle")

    def _translate_constraint(self, constraint, minimum, maximum, path=""):
        """Map a constraint name onto the one STK measures the way it's meant.

        Measured on STK 11 (jobs 0118-0119, 550 km, nadir sensor):
          * ElevationAngle on a SENSOR is measured at the satellite's own
            local horizon -> a ground target is always below it -> 0 access.
            The ground-side angle on a sensor is GroundElevAngle (it gave
            exactly the facility ElevationAngle result, 546 s).
          * Off-nadir on a nadir-pointed sensor is AngleOffBoresight Max
            (553 s, identical to a 45 deg cone).
          * Off-nadir is NOT 90 - elevation: at 550 km, 45 deg off-nadir is
            39.8 deg ground elevation (Earth curvature; depends on altitude).
        So off-nadir is only translated on sensors; on other objects it is
        refused rather than guessed.
        """
        key = str(constraint).replace(" ", "").replace("-", "").replace("_", "").lower()
        on_sensor = "/sensor/" in str(path).replace("\\", "/").lower()
        canonical = self._CONSTRAINT_ALIASES.get(key)
        if key in self._SUPPLEMENTARY_ALIASES:
            if not on_sensor:
                raise ValueError(
                    "'%s' is an angle at the satellite; set it on a nadir-pointed "
                    "sensor (AngleOffBoresight) or give a ground ElevationAngle. "
                    "90 - x is wrong with Earth curvature (45 deg off-nadir at "
                    "550 km = 39.8 deg elevation)." % constraint)
            sys.stderr.write("[fixed] '%s' on a sensor -> AngleOffBoresight "
                             "(assumes the boresight points at nadir).\n" % constraint)
            return "AngleOffBoresight", minimum, maximum
        if on_sensor and (canonical == "ElevationAngle"
                          or constraint == "ElevationAngle"):
            sys.stderr.write(
                "[fixed] ElevationAngle on a sensor is measured at the satellite's "
                "horizon (gives zero ground access);\n"
                "        applied as GroundElevAngle, the elevation seen from the ground.\n")
            return "GroundElevAngle", minimum, maximum
        if canonical is None:
            return constraint, minimum, maximum
        if canonical != constraint:
            sys.stderr.write("[fixed] constraint '%s' -> '%s'\n"
                             % (constraint, canonical))
        return canonical, minimum, maximum

    def set_constraint(self, path, constraint, minimum=None, maximum=None,
                       value=None):
        """Apply an access constraint.

        Examples::

            session.set_constraint(gs, "ElevationAngle", minimum=5)
            session.set_constraint(gs, "Range", maximum=2000)      # km
            session.set_constraint(sat, "Lighting", value="DirectSun")

        Only min-elevation was wrapped before, which quietly made every study
        optimistic: passes counted regardless of range, lighting or sun angle.
        """
        constraint, minimum, maximum = self._translate_constraint(
            constraint, minimum, maximum, path)
        if constraint not in self.KNOWN_CONSTRAINTS:
            sys.stderr.write(
                "[info] '%s' is not among the constraints verified on this STK "
                "(%s); trying it anyway.\n"
                % (constraint, ", ".join(self.KNOWN_CONSTRAINTS)))
        applied = False
        if value is not None:
            applied |= self.cmd("SetConstraint %s %s %s" % (path, constraint, value),
                                label="constraint %s" % constraint, fatal=False)
        if minimum is not None:
            applied |= self.cmd("SetConstraint %s %s Min %.6f" % (path, constraint, float(minimum)),
                                label="constraint %s min" % constraint, fatal=False)
        if maximum is not None:
            applied |= self.cmd("SetConstraint %s %s Max %.6f" % (path, constraint, float(maximum)),
                                label="constraint %s max" % constraint, fatal=False)
        if not applied:
            # A constraint that will not apply makes the study OPTIMISTIC --
            # it does not make it wrong to build. Aborting here cost a whole
            # run and left nine of ten satellites without their sensor, which
            # is far worse than an unapplied 50-degree limit. Say it loudly
            # and carry on; sanity_report() lists what never took.
            sys.stderr.write(
                "[warn] the '%s' constraint would not apply on %s -- results "
                "will be OPTIMISTIC for it.\n"
                "       Verified constraint names on this STK: %s.\n"
                % (constraint, path, ", ".join(self.KNOWN_CONSTRAINTS)))
            self.unapplied_constraints.append((path, constraint))
            return False
        return True

    # -- area targets, coverage, chains, attitude ---------------------------
    #
    # Built against member names read from the type library by
    # probe_object_model.py, not guessed Connect syntax -- every Connect form
    # tried for these was rejected. The object model exposes:
    #   AreaTarget : CommonTasks.SetAreaTypePattern / SetAreaTypeEllipse
    #   Coverage   : Grid.BoundsType/Bounds/Resolution, AssetList.Add,
    #                ComputeAccesses
    #   FOM        : SetDefinitionType, Definition, DefinitionSupportedTypes
    #   Chain      : Objects.Add, ComputeAccess
    #   Attitude   : Attitude.Basic.SetProfileType, ProfileSupportedTypes

    def _supported(self, obj, property_name):
        """Values from a *SupportedTypes property, as a list of ints.

        The property comes back as a nested sequence on STK 11 -- a tuple of
        tuples rather than a flat list of ints -- so flatten before
        converting. Assuming flat gave "int() argument must be ... not
        'tuple'".
        """
        try:
            raw = getattr(obj, property_name)
        except Exception:
            return []

        values = []

        def flatten(item, depth=0):
            if depth > 4:
                return
            if isinstance(item, (list, tuple)):
                for element in item:
                    flatten(element, depth + 1)
                return
            try:
                values.append(int(item))
            except (TypeError, ValueError):
                pass

        flatten(raw)
        return values

    _GATOR_READY = False

    def gator(self, obj, interface):
        """Cast an Astrogator object to a specific interface, e.g.
        gator(segment, "IAgVAMCSManeuver").

        MEASURED on STK 11 (bridge jobs 0097-0106): Astrogator hands back
        GENERIC interfaces, so segment-specific members (Maneuver,
        StoppingConditions...) are invisible until cast. The cast target must
        be the DISPATCH form, whose name has a leading underscore
        (_IAgVAMCSManeuver): the plain IAgVAMCSManeuver is vtable-only and
        unreachable from Python. The bindings must be generated in full --
        on-demand generation omits the interfaces.
        """
        from win32com.client import gencache, CastTo
        if not StkSession._GATOR_READY:
            gencache.EnsureModule(_EXTRA_TYPELIBS[0][0], 0, _EXTRA_TYPELIBS[0][1],
                                  _EXTRA_TYPELIBS[0][2], bForDemand=0)
            StkSession._GATOR_READY = True
        name = interface if interface.startswith("_") else "_" + interface
        try:
            return CastTo(obj, name)
        except Exception as exc:
            raise StkError("cannot cast to %s: %s" % (name, exc))

    def enum_strict(self, name):
        """An enum value by EXACT name, or raise. Never returns None.

        Passing None on to STK is how a missing enum becomes a plausible wrong
        value (None -> 0 -> the first member of the enum).
        """
        if not getattr(self, "_enums", None):
            self._enums = self._load_enums()
        if name not in self._enums:
            near = [n for n in self._enums if n.lower().startswith(name[:12].lower())][:8]
            raise StkError("enum '%s' not found in STK's type libraries. Similar: %s"
                           % (name, ", ".join(near) or "none"))
        return self._enums[name]

    def _enum_choice(self, wanted, supported, *fragments):
        """Resolve a friendly name to an enum value the object supports.

        Raises listing what IS supported, so a wrong name is a one-line fix
        rather than a hunt through STK's documentation.
        """
        value = self.find_enum(wanted, *fragments) if fragments else self.find_enum(wanted)
        if value is None:
            value = self.find_enum(wanted, wanted.replace("e", "", 1).lower())
        if value is not None and (not supported or value in supported):
            return value
        names = []
        if getattr(self, "_enums", None):
            for name, val in sorted(self._enums.items()):
                if val in supported and any(f.lower() in name.lower() for f in (fragments or [""])):
                    names.append("%s (%d)" % (name, val))
        raise StkError(
            "'%s' is not a supported option here.%s"
            % (wanted,
               ("\n  Supported: %s" % ", ".join(names[:12])) if names
               else "\n  Supported enum values: %s" % supported))

    def new_area_target(self, name, boundary, central_body=None):
        """Create an area target (region of interest) from lat/lon points.

        ``boundary`` is a sequence of ``(latitude_deg, longitude_deg)``.
        """
        path = self.unique_path("*/AreaTarget/%s" % name)
        actual = path.rsplit("/", 1)[-1]
        body = central_body or "Earth"
        if not self.cmd("New / */AreaTarget %s CentralBody %s" % (actual, body),
                        fatal=False):
            self.cmd("New / */AreaTarget %s" % actual, label="create area target")
        points = [(float(lat), float(lon)) for lat, lon in boundary]
        if len(points) < 3:
            raise StkError("an area target needs at least 3 boundary points")
        try:
            self.obj(path).CommonTasks.SetAreaTypePattern(points)
        except Exception as exc:
            raise StkError("Failed to set the area-target boundary on %s: %s"
                           % (path, exc))
        return path

    def read_shapefile_rings(self, shp_path, min_vertices=4):
        """Polygon rings from an ESRI .shp file, as [(lat, lon), ...] lists.

        Written against the published shapefile spec rather than a library,
        because this module ships standalone next to a generated script and
        must not require pip installs on the user's machine. Only the
        geometry file is read -- the .dbf attributes are not needed to draw
        a boundary.

        Handles shape types 5 (Polygon), 15 (PolygonZ) and 25 (PolygonM).
        Rings come back LARGEST FIRST, so ``rings[0]`` is the mainland and
        the rest are islands or holes.

        Shapefile coordinates are (x=longitude, y=latitude) and must be
        geographic WGS84 -- a projected file (metres) is detected and
        refused, because its coordinates would land an area target in the
        Gulf of Guinea rather than raising.
        """
        import struct

        projection = os.path.splitext(shp_path)[0] + ".prj"
        if os.path.isfile(projection):
            try:
                with open(projection, "r") as handle:
                    text = handle.read(400)
                if "PROJCS" in text.upper():
                    raise StkError(
                        "%s is a PROJECTED shapefile (its .prj starts with "
                        "PROJCS). STK area targets need geographic "
                        "latitude/longitude -- reproject it to WGS84 "
                        "(EPSG:4326) first." % os.path.basename(shp_path))
            except OSError:
                pass

        with open(shp_path, "rb") as handle:
            data = handle.read()
        if len(data) < 100 or struct.unpack(">i", data[0:4])[0] != 9994:
            raise StkError("%s is not a shapefile (bad magic number)." % shp_path)

        rings = []
        offset = 100                       # past the file header
        while offset + 8 <= len(data):
            _number, length_words = struct.unpack(">ii", data[offset:offset + 8])
            offset += 8
            end = offset + length_words * 2
            shape_type = struct.unpack("<i", data[offset:offset + 4])[0]
            if shape_type in (5, 15, 25):
                num_parts, num_points = struct.unpack(
                    "<ii", data[offset + 36:offset + 44])
                parts_at = offset + 44
                parts = list(struct.unpack(
                    "<%di" % num_parts, data[parts_at:parts_at + 4 * num_parts]))
                points_at = parts_at + 4 * num_parts
                coords = struct.unpack(
                    "<%dd" % (2 * num_points),
                    data[points_at:points_at + 16 * num_points])
                bounds = parts + [num_points]
                for index in range(num_parts):
                    ring = []
                    for point in range(bounds[index], bounds[index + 1]):
                        lon, lat = coords[2 * point], coords[2 * point + 1]
                        ring.append((lat, lon))
                    if len(ring) >= min_vertices:
                        rings.append(ring)
            offset = end

        if not rings:
            raise StkError(
                "No polygon rings found in %s. Area targets need polygons; "
                "a point or polyline shapefile cannot bound a region."
                % os.path.basename(shp_path))
        rings.sort(key=len, reverse=True)
        return rings

    @staticmethod
    def _country_polygons():
        """The shared Natural Earth outlines, or None if unavailable.

        Imported lazily and defensively: country_polygons.py has no imports
        of its own, but it still has to be sitting next to this module.
        """
        try:
            import country_polygons
            return country_polygons._POLY_DATA
        except Exception:
            return None

    def list_countries(self):
        """Country names with a built-in outline. Empty if the data is absent."""
        data = self._country_polygons()
        return sorted(data) if data else []

    def new_area_target_from_country(self, name, country, ring=0,
                                     central_body=None):
        """Area target from the built-in Natural Earth outline of a country.

        Prefer this to typing coordinates. A lat/lon box is the wrong shape
        for a country -- a Greece box also covers Albania and North
        Macedonia, a Japan box covers both Koreas -- and an outline recalled
        from memory is plausible and wrong by tens of kilometres. Either way
        every coverage statistic computed over the region is quietly shifted.

        ``ring`` picks the part, mainland first: Greece is ring 0 (mainland
        and Peloponnese) and ring 1 (Crete). Build one area target per ring
        and pass them all to ``new_coverage(regions=[...])`` when the islands
        matter.

        The data is 1:110m -- right for coverage and revisit work, wrong for
        anything needing a real border. Only the countries listed by
        :meth:`list_countries` are included; for anything else use
        :meth:`new_area_target_from_shapefile`.
        """
        data = self._country_polygons()
        if not data:
            raise StkError(
                "country_polygons.py is not next to stk_helpers.py, so "
                "built-in outlines are unavailable. Use "
                "new_area_target_from_shapefile(), or pass explicit "
                "boundary points to new_area_target().")

        wanted = str(country).strip().lower()
        match = None
        for candidate in data:
            if candidate.lower() == wanted:
                match = candidate
                break
        if match is None:
            # common alternative spellings: USA, UK, Czech Republic, ...
            try:
                import country_polygons
                aliases = getattr(country_polygons, "_ALIASES", {})
            except Exception:
                aliases = {}
            for spelling, canonical in aliases.items():
                if spelling.lower() == wanted and canonical in data:
                    match = canonical
                    sys.stderr.write("[info] '%s' -> '%s'\n" % (country, canonical))
                    break
        if match is None:
            raise StkError(
                "No built-in outline for '%s'. Available: %s.\n"
                "  For any other region use new_area_target_from_shapefile()."
                % (country, ", ".join(sorted(data))))

        rings = data[match]
        if ring >= len(rings):
            raise StkError(
                "'%s' has %d ring(s) (0 = mainland); ring %d was requested."
                % (match, len(rings), ring))

        # country_polygons stores (lon, lat); new_area_target takes (lat, lon)
        points = [(lat, lon) for lon, lat in rings[ring]]
        sys.stderr.write(
            "[info] area target '%s' from the built-in %s outline, ring %d "
            "of %d (%d vertices, Natural Earth 1:110m)\n"
            % (name, match, ring, len(rings), len(points)))
        return self.new_area_target(name, points, central_body=central_body)

    def new_area_target_from_shapefile(self, name, shp_path, ring=0,
                                       max_vertices=400, central_body=None):
        """Area target from a polygon in an ESRI shapefile.

        This is the honest way to bound a region of interest. A country
        outline recalled from memory looks plausible and is quietly wrong by
        tens of kilometres, which shifts every coverage statistic computed
        over it; a lat/lon box is worse still, since a "Greece" box also
        covers Albania and a "Japan" box covers both Koreas.

        ``ring`` selects which polygon to use, largest first -- 0 is the
        mainland. Rings longer than ``max_vertices`` are decimated evenly,
        keeping the first and last point, because a 40,000-vertex coastline
        is slower to draw than it is accurate for coverage work.
        """
        rings = self.read_shapefile_rings(shp_path)
        if ring >= len(rings):
            raise StkError(
                "%s has %d polygon ring(s); ring %d was requested."
                % (os.path.basename(shp_path), len(rings), ring))
        points = rings[ring]

        if max_vertices and len(points) > max_vertices:
            step = len(points) / float(max_vertices)
            thinned = [points[int(index * step)] for index in range(max_vertices)]
            if thinned[-1] != points[-1]:
                thinned[-1] = points[-1]
            sys.stderr.write(
                "[info] %s ring %d has %d vertices; using %d evenly spaced "
                "ones.\n" % (os.path.basename(shp_path), ring, len(points),
                             len(thinned)))
            points = thinned

        latitudes = [lat for lat, _lon in points]
        longitudes = [lon for _lat, lon in points]
        if max(map(abs, latitudes)) > 90.0 or max(map(abs, longitudes)) > 180.0:
            raise StkError(
                "%s ring %d has coordinates outside +-90 / +-180 "
                "(lat %.1f..%.1f, lon %.1f..%.1f) -- it is probably projected "
                "rather than geographic."
                % (os.path.basename(shp_path), ring, min(latitudes),
                   max(latitudes), min(longitudes), max(longitudes)))

        sys.stderr.write(
            "[info] area target '%s' from %s ring %d: %d vertices, "
            "lat %.2f..%.2f, lon %.2f..%.2f\n"
            % (name, os.path.basename(shp_path), ring, len(points),
               min(latitudes), max(latitudes), min(longitudes), max(longitudes)))
        return self.new_area_target(name, points, central_body=central_body)

    def new_area_target_ellipse(self, name, lat_deg, lon_deg,
                                semi_major_km, semi_minor_km, bearing_deg=0.0,
                                central_body=None):
        """Create an elliptical area target centred on a point.

        STK takes the CENTRE from the object's position and the ellipse from
        its axes, so the position is set first and SetAreaTypeEllipse is
        given only (semi-major, semi-minor, bearing). Passing the centre to
        SetAreaTypeEllipse raises "takes from 1 to 4 positional arguments but
        6 were given"; several argument orders are tried so a build with a
        different signature still works.
        """
        path = self.unique_path("*/AreaTarget/%s" % name)
        actual = path.rsplit("/", 1)[-1]
        created = False
        if central_body:
            created = self.cmd("New / */AreaTarget %s CentralBody %s" % (actual, central_body),
                               fatal=False)
        if not created:
            self.cmd("New / */AreaTarget %s" % actual, label="create area target")
            if central_body:
                sys.stderr.write(
                    "[warn] could not set central body '%s' on %s; it will use "
                    "the scenario's body.\n" % (central_body, actual))

        # centre first -- the ellipse is defined about the object's position
        self.cmd("SetPosition %s Geodetic %.9f %.9f 0.0"
                 % (path, float(lat_deg), float(lon_deg)),
                 label="area target centre", fatal=False)

        common = self.obj(path).CommonTasks
        attempts = []
        for description, args in (
                ("(semi_major, semi_minor, bearing)",
                 (float(semi_major_km), float(semi_minor_km), float(bearing_deg))),
                ("(semi_major, semi_minor)",
                 (float(semi_major_km), float(semi_minor_km))),
                ("(lat, lon, semi_major, semi_minor)",
                 (float(lat_deg), float(lon_deg), float(semi_major_km),
                  float(semi_minor_km)))):
            try:
                common.SetAreaTypeEllipse(*args)
                return path
            except Exception as exc:
                attempts.append("    %s -> %s" % (description, str(exc).splitlines()[0]))

        raise StkError(
            "Could not set an elliptical area on %s.\n  Attempts:\n%s\n"
            "  Use new_area_target() with explicit boundary points instead."
            % (path, "\n".join(attempts)))

    def new_chain(self, name, members, compute=True):
        """Create a chain (e.g. satellite -> relay -> ground) and compute it.

        ``members`` is an ordered list of object paths. A chain models the
        end-to-end link, which is not the same as two separate accesses:
        the relay leg and the downlink must overlap in time.
        """
        path = self.unique_path("*/Chain/%s" % name)
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / */Chain %s" % actual, label="create chain")
        chain = self.obj(path)
        for member in members:
            member = self.clean_path(member)
            trimmed = member.lstrip("*/")
            added = False
            for candidate in (trimmed, member):
                for method in ("Add", "AddObject"):
                    try:
                        getattr(chain.Objects, method)(candidate)
                        added = True
                        break
                    except Exception:
                        continue
                if added:
                    break
            if not added:
                raise StkError("Could not add %s to chain %s" % (member, path))
        if compute:
            try:
                chain.ComputeAccess()
            except Exception as exc:
                raise StkError("Chain %s built but ComputeAccess failed: %s" % (path, exc))
        return path

    def new_coverage(self, name, lat_min=None, lat_max=None, lon_min=None,
                     lon_max=None, resolution_deg=2.0, assets=(), compute=True,
                     regions=()):
        """Create a coverage definition and assign assets.

        Two ways to bound the grid:

        ``lat_min/lat_max/lon_min/lon_max``
            A latitude/longitude BOX. Quick, but a box is a box: a "Greece"
            box also covers Albania and North Macedonia, and a "Japan" box
            covers both Koreas. Revisit statistics then average over grid
            points in countries nobody asked about.

        ``regions``
            One or more area-target paths (see :meth:`new_area_target`). The
            grid is confined to those polygons, so the statistics describe
            the actual region. Prefer this whenever the ROI is a country,
            a coastline or any non-rectangular area.

        Passing ``regions`` takes precedence over the box.
        """
        path = self.unique_path("*/CoverageDefinition/%s" % name)
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / */CoverageDefinition %s" % actual, label="create coverage")
        cov = self.obj(path)
        region_paths = [regions] if isinstance(regions, str) else list(regions)
        try:
            grid = cov.Grid
            if region_paths:
                self._bound_grid_to_regions(grid, region_paths, path)
            else:
                if None in (lat_min, lat_max, lon_min, lon_max):
                    raise StkError(
                        "new_coverage needs either a lat/lon box or regions=[...]")
                grid.BoundsType = self._enum_choice(
                    "eBoundsLatLonRegion", self._supported(grid, "BoundsTypeSupportedTypes"),
                    "bounds", "latlon")
                bounds = grid.Bounds
                for attribute, value in (("MinLatitude", lat_min), ("MaxLatitude", lat_max),
                                         ("MinLongitude", lon_min), ("MaxLongitude", lon_max)):
                    try:
                        setattr(bounds, attribute, float(value))
                    except Exception:
                        pass
            grid.ResolutionType = self._enum_choice(
                "eResolutionLatLon", self._supported(grid, "ResolutionTypeSupportedTypes"),
                "resolution", "latlon")
            grid.Resolution.LatLon = float(resolution_deg)
        except StkError:
            raise
        except Exception as exc:
            raise StkError("Failed to configure the coverage grid on %s: %s" % (path, exc))

        for asset in assets:
            trimmed = asset.lstrip("*/")
            for candidate in (trimmed, asset):
                try:
                    cov.AssetList.Add(candidate)
                    break
                except Exception:
                    continue
        if compute:
            try:
                cov.ComputeAccesses()
            except Exception as exc:
                raise StkError("Coverage %s defined but ComputeAccesses failed: %s"
                               % (path, exc))
        return path

    def _bound_grid_to_regions(self, grid, region_paths, coverage_path):
        """Confine a coverage grid to one or more area targets.

        STK spells this "custom regions". The area targets must already
        exist. Several member spellings are tried because the collection is
        named differently across builds; if none work the caller gets a real
        error rather than a grid that silently stayed global.
        """
        bounds_type = self._enum_choice(
            "eBoundsCustomRegions",
            self._supported(grid, "BoundsTypeSupportedTypes"),
            "bounds", "region")
        grid.BoundsType = bounds_type
        bounds = grid.Bounds

        collection = None
        for member in ("AreaTargets", "RegionList", "Regions"):
            if hasattr(bounds, member):
                collection = getattr(bounds, member)
                break
        if collection is None:
            raise StkError(
                "This STK's coverage bounds expose no area-target collection "
                "(tried AreaTargets, RegionList, Regions) on %s." % coverage_path)

        for region in region_paths:
            trimmed = str(region).lstrip("*/")
            added = False
            for candidate in (trimmed, str(region)):
                for method in ("Add", "AddObject"):
                    if not hasattr(collection, method):
                        continue
                    try:
                        getattr(collection, method)(candidate)
                        added = True
                        break
                    except Exception:
                        continue
                if added:
                    break
            if not added:
                raise StkError(
                    "Could not add region %s to the coverage grid of %s."
                    % (region, coverage_path))
        return True

    def grid_point_count(self, coverage_path):
        """How many grid points a coverage definition has, or None.

        MEASURED on STK 11 by probe_coverage_grid.py: the Grid object exposes
        no count at all -- PointsElementCount, Points, PointDefinition,
        NumberOfPoints and every collection method are absent, before and
        after ComputeAccesses. The count comes from the "Grid Point
        Locations" data provider instead, whose row count is the number of
        points.

        Worth printing whenever a grid is bounded by a region: a country
        polygon at a coarse resolution can collapse to a handful of points,
        which makes any statistic over it meaningless without saying so.
        """
        try:
            provider = self.obj(coverage_path).DataProviders.Item(
                "Grid Point Locations")
        except Exception:
            return None

        group = provider
        for accessor in (lambda p: p.Group.Item("Fixed"),
                         lambda p: p.Group.Item(0)):
            try:
                group = accessor(provider)
                break
            except Exception:
                continue

        when = getattr(self, "_start", None)
        calls = [lambda g: g.Exec(),
                 lambda g: g.ExecElements(["Latitude", "Longitude"])]
        if when:
            calls.insert(1, lambda g: g.Exec(when, when))
        for call in calls:
            try:
                result = call(group)
            except Exception:
                continue
            try:
                datasets = result.DataSets
                if datasets.Count == 0:
                    continue
                return len(datasets.Item(0).GetValues())
            except Exception:
                continue
        return None

    def add_figure_of_merit(self, coverage_path, name, definition="Revisit Time"):
        """Add a figure of merit to a coverage definition.

        ``definition`` is a friendly name such as "Revisit Time", "Coverage
        Time", "Response Time", "Number of Accesses". An unsupported one
        raises with the supported list.
        """
        path = self.unique_path("%s/FigureOfMerit/%s" % (coverage_path, name))
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / %s/FigureOfMerit %s" % (coverage_path, actual),
                 label="create figure of merit")
        fom = self.obj(path)
        fragments = [w for w in str(definition).replace("_", " ").split() if w]
        value = self._enum_choice("e" + "".join(w.capitalize() for w in fragments),
                                  self._supported(fom, "DefinitionSupportedTypes"),
                                  *fragments)
        try:
            fom.SetDefinitionType(value)
        except Exception as exc:
            raise StkError("Failed to set figure-of-merit definition on %s: %s"
                           % (path, exc))
        return path

    @staticmethod
    def _profile_words(enum_name):
        """eProfileNadirAlignmentWithECIVelocityConstraint ->
        "Nadir Alignment With ECI Velocity Constraint"."""
        core = enum_name[len("eProfile"):] if enum_name.startswith("eProfile") else enum_name[1:]
        return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", core)

    def attitude_profiles(self, sat_path=None):
        """{readable name: enum name} for every basic attitude profile."""
        if not getattr(self, "_enums", None):
            self._enums = self._load_enums()
        names = {self._profile_words(k): k for k in self._enums
                 if k.startswith("eProfile") and k != "eProfileUnknown"}
        names["Coordinated Turn"] = "eCoordinatedTurn"
        return names

    def set_attitude_profile(self, sat_path, profile="Nadir Alignment With ECI Velocity Constraint"):
        """Set the spacecraft's attitude profile (the bus, not a sensor).

        ``profile`` must match one of :meth:`attitude_profiles` EXACTLY (case
        and spacing ignored) -- or be the enum name itself.

        MEASURED (bridge job 0089): the previous version matched WORDS in any
        order, and "Nadir Alignment With ECI Velocity Constraint" set
        eProfileECIVelocityAlignmentWithNadirConstraint -- the same words, the
        opposite meaning. Identical on a circular orbit; on an eccentric one
        the nadir sensor is off by the flight-path angle. Word order matters.
        """
        try:
            basic = self.obj(sat_path).Attitude.Basic
        except Exception as exc:
            raise StkError("No basic attitude on %s: %s" % (sat_path, exc))
        table = self.attitude_profiles()
        # compare with spaces removed: order-SENSITIVE (letters must come in
        # the same order) but forgiving of "ECI Z Axis" vs "ECIZ Axis"
        squash = lambda text: "".join(str(text).replace("_", " ").split()).lower()
        wanted = squash(profile)
        enum_name = next((e for readable, e in table.items()
                          if squash(readable) == wanted or e.lower() == wanted
                          or squash(e[len("eProfile"):]) == wanted), None)
        if enum_name is None:
            raise StkError("attitude profile '%s' unknown. Profiles:\n  %s"
                           % (profile, "\n  ".join(sorted(table))))
        value = self._enums[enum_name]
        try:
            basic.SetProfileType(value)
        except Exception as exc:
            raise StkError("Failed to set attitude profile %s on %s: %s"
                           % (enum_name, sat_path, exc))
        if int(basic.ProfileType) != int(value):
            raise StkError("%s: asked %s, STK reports profile %s"
                           % (sat_path, enum_name, basic.ProfileType))
        return enum_name

    def write_pass_list_csv(self, from_path, to_path, out_dir=None, filename=None,
                            with_details=False):
        """Write EVERY pass, not just totals: start, stop, duration, gap.

        With ``with_details`` each pass also gets peak elevation and minimum
        range, which costs an AER query per pass.
        """
        count, _total, intervals = self.compute_access(from_path, to_path)
        from_name = from_path.rsplit("/", 1)[-1]
        to_name = to_path.rsplit("/", 1)[-1]
        if not filename:
            filename = "passes_%s_%s.csv" % (from_name, to_name)
        out_file = os.path.join(self.output_dir(out_dir), filename)

        header = "Pass,Start,Stop,Duration_s,GapSincePrevious_s"
        if with_details:
            header += ",PeakElevation_deg,MinRange_km"
        lines = [header]
        previous_end = None
        for index, (start_time, stop_time) in enumerate(intervals, 1):
            begins = float(self.root.ConversionUtility.ConvertDate("UTCG", "EpSec", start_time))
            ends = float(self.root.ConversionUtility.ConvertDate("UTCG", "EpSec", stop_time))
            gap = "" if previous_end is None else "%.3f" % (begins - previous_end)
            previous_end = ends
            row = "%d,%s,%s,%.3f,%s" % (index, start_time, stop_time, ends - begins, gap)
            if with_details:
                detail = self.pass_details(from_path, to_path, start_time, stop_time) or {}
                row += ",%s,%s" % (
                    "%.3f" % detail["peak_elevation_deg"] if detail.get("peak_elevation_deg") is not None else "",
                    "%.3f" % detail["min_range_km"] if detail.get("min_range_km") is not None else "")
            lines.append(row)
        with open(out_file, "w") as handle:
            handle.write("\n".join(lines) + "\n")
        print("[file] %s  (%d passes)" % (out_file, count))
        return out_file

    # -- ground assets ------------------------------------------------------
    def central_body_of(self, path):
        """The central body STK says an object is on, or None."""
        for getter in ("CentralBodyName", "CentralBody"):
            try:
                value = getattr(self.obj(path), getter)
                text = str(value).strip()
                if text and not text.startswith("<"):
                    return text
            except Exception:
                continue
        try:
            return str(self.root.ExecuteCommand("GetCentralBody %s" % path).Item(0)).strip()
        except Exception:
            return None

    def _assert_central_body(self, path, expected):
        """Fail loudly if an object landed on the wrong body.

        STK's Connect "New" carries the LAST central body used forward when
        none is given. A scenario that created a lunar site and then a ground
        station without saying "Earth" put the ground station on the Moon --
        while the script printed "central body: Earth", because it reported
        what it asked for rather than what STK built. Everything downstream
        (access, chains) was then quietly wrong.
        """
        actual = self.central_body_of(path)
        if actual and expected and actual.lower() != str(expected).lower():
            raise StkError(
                "%s was created on %s, but %s was requested. STK carries the "
                "previous central body forward when one is not specified, so "
                "this is usually a leftover from an earlier object."
                % (path, actual, expected))
        return actual

    def new_facility(self, name, lat_deg, lon_deg, alt_km=0.0, central_body=None):
        """Create a surface site at a geodetic position. Altitude in km.

        Works on ANY central body, not just Earth: pass
        ``central_body="Moon"`` for a lunar landing site or station. Verified
        on STK 11 -- Facility, Place and Target all accept a non-Earth body.
        Do NOT hand-write a Connect command for this. STK has no
        "New / */CentralBody/Moon Facility ..." and no
        "New / */Planet/Moon Facility ..." form -- both have been tried and
        rejected -- and session.cmd() now refuses hand-written creation
        commands outright. This helper is the only route.

        Leaving central_body out puts the coordinates on EARTH, which is how
        a Mare Crisium landing site once ended up in the Arabian Sea.
        """
        path = self.unique_path("*/Facility/%s" % name)
        actual = path.rsplit("/", 1)[-1]
        # ALWAYS name the body. Omitting it lets STK reuse whatever body was
        # last used, which silently put an Earth ground station on the Moon.
        body = central_body or "Earth"
        created = self.cmd("New / */Facility %s CentralBody %s" % (actual, body),
                           label="create facility on %s" % body, fatal=False)
        if not created:
            self.cmd("New / */Facility %s" % actual, label="create facility")
        self.cmd("SetPosition %s Geodetic %.9f %.9f %.9f"
                 % (path, float(lat_deg), float(lon_deg), float(alt_km)),
                 label="facility position")
        self._assert_central_body(path, body)
        return path

    def new_planet(self, name):
        """Add a planet/moon as an ephemeris reference object (e.g. Earth in
        a Moon-centred scenario, for Earth-pointing geometry or AER)."""
        path = self.unique_path("*/Planet/%s" % name)
        actual = path.rsplit("/", 1)[-1]
        self.cmd("New / */Planet %s" % actual, label="create planet")
        return path

    def set_min_elevation(self, path, min_elev_deg):
        """Apply a minimum elevation-angle access constraint."""
        self.cmd("SetConstraint %s ElevationAngle Min %.6f" % (path, float(min_elev_deg)),
                 label="min elevation constraint", fatal=False)

    # -- data providers: every output STK can derive -------------------------
    #
    # MEASURED on STK 11 by bridge job 0041: every provider is one of three
    # kinds, told apart by the bits of its Type (AgEDataProviderType):
    #   fixed        (1)  Exec()                         e.g. a facility's position
    #   time-varying (2)  Exec(start, stop, step)        e.g. Classical Elements
    #   interval     (4)  Exec(start, stop)              e.g. Lighting Times, Access
    # Many providers are split into groups (reference frames for positions,
    # Sunlight/Penumbra/Umbra for lighting). A satellite alone has 192
    # providers; the census in bridge/census/ lists them for every object type.

    _DP_KINDS = ((4, "interval"), (1024, "interval"), (2, "time-varying"), (1, "fixed"))

    def data_providers(self, path):
        """Every data provider on an object: [(name, kind, [groups])]."""
        listing = []
        providers = self.obj(path).DataProviders
        for index in range(providers.Count):
            provider = providers.Item(index)
            groups = []
            try:
                group = provider.Group
                groups = [str(group.Item(g).Name) for g in range(group.Count)]
            except Exception:
                pass
            listing.append((str(provider.Name), self._dp_kind(provider), groups))
        return listing

    def _dp_kind(self, provider):
        try:
            raw = int(provider.Type)
        except Exception:
            return "unknown"
        for bit, kind in self._DP_KINDS:
            if raw & bit:
                return kind
        return "unknown"

    @staticmethod
    def _closest(wanted, available, limit=6):
        wanted_words = set(str(wanted).lower().replace("-", " ").split())
        scored = []
        for candidate in available:
            words = set(candidate.lower().replace("-", " ").split())
            shared = len(wanted_words & words)
            if shared or str(wanted).lower() in candidate.lower():
                scored.append((shared, candidate))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [candidate for _shared, candidate in scored[:limit]]

    def read_data(self, path, provider, group=None, elements=None,
                  start=None, stop=None, step_sec=60.0, pre_data=None):
        """Read ANY STK data provider on any object. Returns a DataResult.

        This is the general way to get results out of STK: positions,
        orbital elements, lighting, access, AER, link budgets, coverage and
        figure-of-merit statistics -- anything in the object's Report & Graph
        Manager. The provider's kind decides how it is executed; the caller
        never needs to know it.

            r = session.read_data(sat, "Classical Elements", group="J2000",
                                  elements=["Time", "Semi-major Axis"])
            r.column("Semi-major Axis")
            r = session.read_data(fom, "Overall Value")      # min/max/avg
            r = session.read_data(sat, "Lighting Times", group="Umbra")
            if r.empty: print(r.message)   # "No Umbra Times." is an answer

        ``pre_data`` is an input some providers require before they can run:
        a CommSystem's "Link Information" / "Interference Information" need
        the victim receiver's path ("Facility/GS/Receiver/Rx"). STK says so
        in the result message ("Must specify a valid receiver or transmitter
        path as the PreData") rather than raising.
        ``start``/``stop`` default to the scenario's analysis period.
        ``step_sec`` applies to time-varying providers only.
        A wrong provider or group name raises listing the closest real ones.
        """
        # ``path`` may also be an STK object that is not addressable by path,
        # such as an Access -- the link budget lives there.
        owner = path if hasattr(path, "DataProviders") else self.obj(path)
        if not isinstance(path, str):
            path = getattr(path, "_label", "access")
        # Look up by NAME first; list the alternatives only when that fails.
        # MEASURED (bridge job 0091): listing every name to validate cost ~40 s
        # per read on a satellite (192 providers, ~80 Vectors(Body) groups,
        # each name a separate cross-process call) -- versus a direct lookup.
        providers = owner.DataProviders
        match, dp = str(provider), None
        try:
            dp = providers.Item(match)
            match = str(dp.Name)
        except Exception:
            names = [str(providers.Item(i).Name) for i in range(providers.Count)]
            exact = next((n for n in names if n.lower() == str(provider).lower()), None)
            if exact is None:
                raise StkError(
                    "%s has no data provider '%s'. Closest: %s.\n"
                    "  List them all with session.data_providers(%r)."
                    % (path, provider, ", ".join(self._closest(provider, names)) or "none",
                       path))
            match, dp = exact, providers.Item(exact)
        kind = self._dp_kind(dp)

        target = dp
        if group is not None:
            try:
                target = dp.Group.Item(str(group))
                group = str(group)
            except Exception:
                try:
                    groups = dp.Group
                    group_names = [str(groups.Item(g).Name) for g in range(groups.Count)]
                except Exception:
                    group_names = []
                gmatch = next((g for g in group_names if g.lower() == str(group).lower()), None)
                if gmatch is None:
                    raise StkError(
                        "'%s' has no group '%s'. Groups: %s."
                        % (match, group, ", ".join(group_names) or "none (omit group=)"))
                target = dp.Group.Item(gmatch)
                group = gmatch

        if pre_data is not None:
            try:
                target.PreData = str(pre_data).lstrip("*/")
            except Exception as exc:
                raise StkError("'%s' does not accept PreData: %s"
                               % (match, self._com_reason(exc)))
        start = start or getattr(self, "_start", None)
        stop = stop or getattr(self, "_stop", None)
        # STK refuses the whole call on a repeated element name ("Duplicate
        # name was found in an Elements array"), so keep the first of each.
        elements = list(dict.fromkeys(elements)) if elements else None
        try:
            if kind == "time-varying":
                result = (target.ExecElements(start, stop, float(step_sec), elements)
                          if elements else target.Exec(start, stop, float(step_sec)))
            elif kind == "interval":
                result = (target.ExecElements(start, stop, elements)
                          if elements else target.Exec(start, stop))
            else:
                result = target.ExecElements(elements) if elements else target.Exec()
        except pywintypes.com_error as exc:
            raise StkError("Reading '%s'%s on %s failed: %s"
                           % (match, (" [%s]" % group) if group else "", path,
                              self._com_reason(exc)))

        try:
            message = "; ".join(str(result.Message.Item(i))
                                for i in range(result.Message.Count)) or "OK"
        except Exception:
            message = "OK"

        def columns_of(datasets):
            out = {}
            for i in range(datasets.Count):
                ds = datasets.Item(i)
                out[str(ds.ElementName)] = list(ds.GetValues())
            return out

        # Gather rows across EVERY result interval (see DataResult): the
        # top-level DataSets hold only the first one, which for access data
        # is only the first pass.
        columns, segments = {}, []
        try:
            for i in range(result.Intervals.Count):
                item = result.Intervals.Item(i)
                cols = columns_of(item.DataSets)
                first = max((len(v) for v in columns.values()), default=0)
                count = max((len(v) for v in cols.values()), default=0)
                for name, values in cols.items():
                    columns.setdefault(name, [None] * first)
                    columns[name].extend(values)
                for name in columns:
                    if name not in cols:
                        columns[name].extend([None] * count)
                segments.append((str(item.StartTime), str(item.StopTime), first, count))
        except Exception:
            columns, segments = {}, []
        if not columns:
            try:
                columns = columns_of(result.DataSets)
            except Exception:
                columns = {}

        intervals = []
        if kind == "interval":
            starts = next((v for k, v in columns.items() if k.lower() == "start time"), [])
            stops = next((v for k, v in columns.items() if k.lower() == "stop time"), [])
            intervals = [(str(a), str(b)) for a, b in zip(starts, stops)]
        return DataResult(path, match, group, kind, message, columns, intervals,
                          segments)

    def write_data_csv(self, data, filename=None, out_dir=None):
        """Write a DataResult to CSV in the output folder. Returns the path."""
        import csv
        if not filename:
            safe = "".join(c if c.isalnum() or c in "-_" else "_"
                           for c in "%s_%s%s" % (data.path.rsplit("/", 1)[-1],
                                                 data.provider,
                                                 ("_" + data.group) if data.group else ""))
            filename = safe + ".csv"
        path = os.path.join(self.output_dir(out_dir), filename)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            names = list(data.columns)
            writer.writerow(names)
            for row in data.rows():
                writer.writerow([row[n] for n in names])
        print("[file] %s  (%d rows; %s)" % (path, len(data), data.message))
        return path

    # -- STK's own reports and graphs ---------------------------------------
    #
    # Syntax measured by probe_report_styles.py on STK 11:
    #   report window : ReportCreate <path> Type Display Style "<style>"
    #   report to file: ReportCreate <path> Type Export  Style "<style>"
    #                                      File "<file>" TimeStep <sec>
    #   graph window  : GraphCreate  <path> Type Display Style "<style>"
    # "Type Graph" and a bare "Graph" command are both rejected.
    #
    # Style names must match STK's own exactly. "Classical Elements" is
    # rejected; the real name is "Classical Orbit Elements". Rather than let
    # a caller guess, report_styles() enumerates what this install has and
    # the helpers below check against it, suggesting near matches on a miss.

    @staticmethod
    def _object_type(path):
        """'*/Satellite/Sat1/Sensor/Cam' -> 'Sensor'."""
        parts = [p for p in str(path).split("/") if p and p != "*"]
        return parts[-2] if len(parts) >= 2 else ""

    def report_styles(self, object_type):
        """Style names this install offers for an object type, sorted.

        Read from STK's own style files (.rst) under STKHome, so the list is
        whatever this machine actually has, including any custom styles.
        """
        cache = getattr(self, "_style_cache", None)
        if cache is None:
            cache = self._style_cache = {}
        key = str(object_type).lower()
        if key in cache:
            return cache[key]

        home = self.directory("STKHome")
        names = set()
        if home and os.path.isdir(home):
            for dirpath, _dirnames, filenames in os.walk(home):
                if os.path.basename(dirpath).lower() != key:
                    continue
                for filename in filenames:
                    stem, ext = os.path.splitext(filename)
                    if ext.lower() == ".rst":
                        names.add(stem)
        cache[key] = sorted(names)
        return cache[key]

    def _check_style(self, path, style):
        """Raise a helpful error if a style name is not one this STK has."""
        object_type = self._object_type(path)
        available = self.report_styles(object_type)
        if not available or style in available:
            return style
        # Some styles live in their own folder rather than the object's --
        # "Access" is a satellite-usable style but is filed under Access.
        # Only reject a name that exists nowhere.
        for folder in ("Access", "Chain", "CoverageDefinition", "FigureOfMerit",
                       "Sensor", "Facility", "AreaTarget"):
            if style in self.report_styles(folder):
                return style
        # Substring matching missed the case it exists for: "Classical
        # Elements" is not a substring of "Classical Orbit Elements". Score by
        # shared words instead.
        wanted_words = set(style.lower().replace("-", " ").split())
        scored = []
        for candidate in available:
            words = set(candidate.lower().replace("-", " ").split())
            shared = len(wanted_words & words)
            if shared:
                scored.append((shared / float(len(wanted_words | words)), shared, candidate))
        scored.sort(reverse=True)
        near = [candidate for _ratio, _shared, candidate in scored]
        # A style name only chooses which COLUMNS are exported -- it cannot
        # make the scenario wrong. So when one candidate is a clear winner,
        # use it and say so, rather than aborting a run that has already
        # built everything correctly.
        if scored and scored[0][0] >= 0.5 and (
                len(scored) == 1 or scored[0][0] > scored[1][0]):
            chosen = scored[0][2]
            sys.stderr.write(
                "[fixed] '%s' is not a %s report style here; using '%s'\n"
                "        (closest match; list them with "
                "session.report_styles('%s'))\n"
                % (style, object_type, chosen, object_type))
            return chosen
        raise StkError(
            "'%s' is not a %s report style in this STK installation.%s\n"
            "  %d styles are available; list them with "
            "session.report_styles(%r)."
            % (style, object_type,
               ("\n  Did you mean: %s?" % ", ".join(near[:4])) if near else "",
               len(available), object_type))

    def open_report(self, path, style):
        """Open one of STK's own report windows on an object."""
        style = self._check_style(path, style)
        self.cmd('ReportCreate %s Type Display Style "%s"' % (path, style),
                 label="open report '%s'" % style)
        return style

    def open_graph(self, path, style):
        """Open one of STK's own graph windows on an object.

        Same style names as reports -- STK draws the style's data as a graph
        rather than a table.
        """
        style = self._check_style(path, style)
        self.cmd('GraphCreate %s Type Display Style "%s"' % (path, style),
                 label="open graph '%s'" % style)
        return style

    def export_report(self, path, style, filename=None, time_step_sec=60,
                      out_dir=None):
        """Write one of STK's own reports to a file. Returns the file path."""
        style = self._check_style(path, style)
        if not filename:
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in style)
            filename = "%s_%s.csv" % (path.rsplit("/", 1)[-1], safe)
        out_file = os.path.join(self.output_dir(out_dir), filename)
        self.cmd('ReportCreate %s Type Export Style "%s" File "%s" TimeStep %g'
                 % (path, style, out_file, float(time_step_sec)),
                 label="export report '%s'" % style)
        print("[file] %s" % out_file)
        return out_file

    # -- deciding HOW to deliver, rather than leaving it to prose ----------
    #
    # "Open a window per object, unless there are many" is a rule with a
    # threshold. Left in the prompt it gets applied outside the range it was
    # written for -- three satellites produced one access report because the
    # guidance had been tuned for twenty-four. A threshold belongs in code
    # where it can be tested, not in a sentence.

    #: Default cutoff for ``windows="auto"``. The script can always override
    #: it, or bypass it entirely with windows=True / windows=False.
    WINDOW_LIMIT = 5

    def publish_report(self, objects, style, windows="auto", files="auto",
                       window_limit=None, time_step_sec=600, label=""):
        """Deliver a report for one or more objects. The CALLER decides how.

        ``windows`` and ``files`` each take:

          True    -- do it for every object
          False   -- do not do it at all
          "auto"  -- windows for up to ``window_limit`` objects (default
                     :attr:`WINDOW_LIMIT`), then files instead

        "auto" is a convenience default, not a policy: a script that knows
        what it wants should say so. Returns
        ``{"windows": [...], "files": [...]}``.
        """
        paths = [objects] if isinstance(objects, str) else list(objects)
        limit = self.WINDOW_LIMIT if window_limit is None else window_limit
        produced = {"windows": [], "files": []}

        for index, path in enumerate(paths):
            if windows is True:
                want_window = True
            elif windows is False:
                want_window = False
            else:
                want_window = len(paths) <= limit or index < 2

            if want_window:
                self.open_report(path, style)
                produced["windows"].append(path)

            if files is True or (files == "auto" and not want_window):
                produced["files"].append(
                    self.export_report(path, style, time_step_sec=time_step_sec))

        self._record_output("report:%s%s" % (style, (" (%s)" % label) if label else ""),
                            len(produced["windows"]), len(produced["files"]))
        return produced

    def publish_access(self, satellites, station, windows="auto",
                       pass_lists=True, totals=True, window_limit=None,
                       with_details=True):
        """Deliver access results. Every part is opt-out.

        ``windows``     -- STK access report windows (True / False / "auto")
        ``pass_lists``  -- a per-pass CSV per satellite
        ``totals``      -- one summary CSV of totals per satellite

        Nothing here is mandatory: ask for exactly the pieces the request
        needs.
        """
        paths = [satellites] if isinstance(satellites, str) else list(satellites)
        limit = self.WINDOW_LIMIT if window_limit is None else window_limit
        produced = {"windows": [], "files": []}

        for index, path in enumerate(paths):
            if pass_lists:
                produced["files"].append(
                    self.write_pass_list_csv(path, station, with_details=with_details))
            if windows is True:
                want_window = True
            elif windows is False:
                want_window = False
            else:
                want_window = len(paths) <= limit or index < 2
            if want_window:
                self.open_access_report(path, station)
                produced["windows"].append(path)

        if totals:
            rows = self.access_summary(paths, station)
            produced["files"].append(
                self.write_access_csv(rows, station.rsplit("/", 1)[-1]))

        self._record_output("access -> %s" % station.rsplit("/", 1)[-1],
                            len(produced["windows"]), len(produced["files"]))
        return produced

    # -- what did this script actually produce? -----------------------------
    def declare_deliverables(self, *asked_for):
        """State what the user asked for, e.g. "passes", "revisit", "graphs".

        Used only by :meth:`output_summary`, which compares this against what
        was produced and flags extras. The helper cannot know the request --
        the script has to say -- but once said, over-production becomes
        visible instead of being noticed by the user in a folder listing.
        """
        self._deliverables = [str(a).strip().lower() for a in asked_for if a]
        return self._deliverables

    def _record_output(self, description, windows=0, files=0):
        if getattr(self, "_outputs", None) is None:
            self._outputs = []
        self._outputs.append((description, windows, files))

    def output_summary(self):
        """Print everything produced, and flag anything not asked for."""
        outputs = getattr(self, "_outputs", []) or []
        print("\n--- outputs ---")
        if not outputs:
            print("  (nothing recorded)")
            return outputs
        total_windows = total_files = 0
        for description, windows, files in outputs:
            total_windows += windows
            total_files += files
            print("  %-44s %d window(s), %d file(s)" % (description, windows, files))
        print("  %-44s %d window(s), %d file(s)" % ("TOTAL", total_windows, total_files))

        asked = getattr(self, "_deliverables", None)
        if asked:
            print("  asked for: %s" % ", ".join(asked))
            unmatched = [d for d, _w, _f in outputs
                         if not any(word in d.lower() for word in asked)]
            if unmatched:
                print("  [check] produced without being asked for:")
                for description in unmatched:
                    print("      %s" % description)
        return outputs

    # -- architecture trade studies -------------------------------------------
    #
    # "What is the best constellation for X?" is answered by SEARCHING, not
    # guessing: every candidate is built in STK (Walker from a seed with a
    # nadir sensor) and scored by STK's own figures of merit over the region.
    # The coverage grid is built ONCE; only the assets change between
    # candidates. Work is chunked by a time budget and saved to a JSON state
    # file, so a search of any size fits the 180 s step limit: call again with
    # the same state_file until it returns done=True.

    @staticmethod
    def sso_inclination_deg(altitude_km):
        """Sun-synchronous inclination for a circular orbit (J2 nodal drift
        matched to 360 deg per tropical year)."""
        import math
        mu, re, j2 = 398600.4418, EARTH_RADIUS_KM, 1.08262668e-3
        a = re + float(altitude_km)
        n = math.sqrt(mu / a ** 3)
        target = 2 * math.pi / (365.2422 * 86400.0)
        c = -target / (1.5 * n * j2 * (re / a) ** 2)
        if not -1.0 <= c <= 1.0:
            raise StkError("no sun-synchronous orbit at %.0f km" % altitude_km)
        return math.degrees(math.acos(c))

    @staticmethod
    def candidate_grid(altitudes_km, total_sats, planes, inclinations_deg=("sso",),
                       half_angles_deg=(None,), phase_f=(None,)):
        """All Walker candidates from lists of options. Plane counts that do
        not divide the satellite total are skipped. inclination "sso" = the
        sun-synchronous value for that altitude. phase_f None = 1 (or 0 for
        one plane)."""
        out = []
        for alt in altitudes_km:
            for inc in inclinations_deg:
                for half in half_angles_deg:
                    for t in total_sats:
                        for p in planes:
                            if p > t or t % p:
                                continue
                            for f in phase_f:
                                ff = (1 if p > 1 else 0) if f is None else int(f)
                                if not 0 <= ff < p:
                                    continue
                                out.append({"altitude_km": float(alt), "inclination": inc,
                                            "half_angle_deg": half, "total_sats": int(t),
                                            "planes": int(p), "phase_f": ff})
        return out

    @staticmethod
    def repeat_ground_track_altitudes(min_alt_km=400.0, max_alt_km=800.0, max_days=3,
                                      inclination_deg="sso"):
        """Altitudes where the ground track repeats exactly after ``days``
        and ``revs`` orbits (circular orbit, J2 secular rates) -- solved, not
        sampled, so they land on the exact altitude (sub-metre), not a grid.

        A repeating track is the classic analytical seed for EO design: the
        same places are re-visited on a fixed cycle. Returns a list of dicts
        {altitude_km, inclination_deg, revs, days} sorted by altitude.
        """
        import math
        mu, re, j2 = 398600.4418, EARTH_RADIUS_KM, 1.08262668e-3
        we = 7.2921158553e-5
        sso_rate = 2 * math.pi / (365.2422 * 86400.0)

        def inc_for(a):
            if str(inclination_deg).lower() != "sso":
                return math.radians(float(inclination_deg))
            n = math.sqrt(mu / a ** 3)
            return math.acos(-sso_rate / (1.5 * n * j2 * (re / a) ** 2))

        def mismatch(a, revs, days):
            n = math.sqrt(mu / a ** 3)
            i = inc_for(a)
            k = j2 * (re / a) ** 2
            node = -1.5 * n * k * math.cos(i)
            arg_lat = n * (1 + 0.75 * k * (3 * math.cos(i) ** 2 - 1)) + \
                0.75 * n * k * (5 * math.cos(i) ** 2 - 1)
            nodal_period = 2 * math.pi / arg_lat
            nodal_day = 2 * math.pi / (we - node)
            return revs * nodal_period - days * nodal_day

        out = []
        lo_a, hi_a = re + float(min_alt_km), re + float(max_alt_km)
        for days in range(1, int(max_days) + 1):
            for revs in range(1, 20 * days):
                if math.gcd(revs, days) != 1:
                    continue
                f_lo, f_hi = mismatch(lo_a, revs, days), mismatch(hi_a, revs, days)
                if f_lo * f_hi > 0:
                    continue
                a, b = lo_a, hi_a
                for _ in range(80):                       # bisection to ~mm
                    m = 0.5 * (a + b)
                    if mismatch(a, revs, days) * mismatch(m, revs, days) <= 0:
                        b = m
                    else:
                        a = m
                sma = 0.5 * (a + b)
                out.append({"altitude_km": round(sma - re, 4),
                            "inclination_deg": round(math.degrees(inc_for(sma)), 4),
                            "revs": revs, "days": days})
        return sorted(out, key=lambda r: r["altitude_km"])

    @staticmethod
    def refine_candidates(best, altitude_span_km, altitude_step_km, all_phasings=True):
        """Candidates around a winning design for the next, finer search level:
        altitudes best +- span at ``step`` (e.g. 50 -> 10 -> 2 -> 0.5 km), and
        every Walker phasing 0..P-1. Feed them to trade_study with the SAME
        state file; designs already scored are skipped automatically."""
        c = dict(best["candidate"] if "candidate" in best else best)
        alt0, p = float(c["altitude_km"]), int(c["planes"])
        n = int(round(float(altitude_span_km) / float(altitude_step_km)))
        alts = [round(alt0 + k * float(altitude_step_km), 4) for k in range(-n, n + 1)]
        phases = range(p) if all_phasings else [int(c["phase_f"])]
        out = []
        for a in alts:
            for f in phases:
                d = dict(c)
                d.update(altitude_km=a, phase_f=int(f))
                out.append(d)
        return out

    def search(self, name, candidates, build, score, objectives, budget_s=140.0,
               state_file=None):
        """THE trade-study recipe -- one engine for every regime.

        The agent decides WHAT to try and HOW to judge it; this engine does the
        rest the same way every time: builds each candidate in STK, scores it,
        removes it, saves progress, resumes, and returns the Pareto front.

        ``candidates``  list of plain dicts (any keys: altitude, planes, slot,
                        antenna size, burn time...). Use candidate_grid,
                        repeat_ground_track_altitudes and refine_candidates,
                        or build the list yourself.
        ``build(cand)`` creates the design in STK, returns the list of object
                        paths it created (they are unloaded afterwards).
        ``score(cand, objects)``  returns {metric: number or None} read FROM STK
                        (None = infeasible). Any metrics: revisit, service %,
                        link margin, delta-v, fuel, cost...
        ``objectives``  [(metric, "min" | "max"), ...] -- the Pareto front is
                        taken over these (e.g. [("cost", "min"),
                        ("worst_revisit_h", "min")]).

        Chunked by ``budget_s`` and saved to ``state_file`` (JSON): call again
        with the same arguments until done=True. Each step is a fresh process,
        so define build/score again in every call -- same code, same results.
        Returns {"done", "evaluated", "remaining", "results", "pareto",
        "state_file"}.
        """
        import json, time
        started = time.time()
        state_file = state_file or os.path.join(self.output_dir(), "search_%s.json" % name)
        state = {"results": []}
        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        key = lambda c: json.dumps(c, sort_keys=True, default=str)
        seen = {key(r["candidate"]) for r in state["results"]}
        for cand in [c for c in candidates if key(c) not in seen]:
            if time.time() - started > float(budget_s):
                break
            row, objects = {"candidate": cand}, []
            try:
                objects = list(build(cand) or [])
                row["metrics"] = dict(score(cand, objects) or {})
            except Exception as exc:
                row["error"] = str(exc)[:300]
            finally:
                for path in reversed(objects):
                    if path and self.exists(path):
                        self.cmd("Unload / %s" % path, label="remove candidate", fatal=False)
            state["results"].append(row)
            seen.add(key(cand))
            with open(state_file, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=1, default=str)
            print("[search] %d: %s -> %s" % (len(state["results"]), cand,
                                             row.get("metrics", row.get("error"))))

        def value(r, m):
            v = r.get("metrics", {}).get(m)
            return None if v is None else float(v)
        feasible = [r for r in state["results"]
                    if all(value(r, m) is not None for m, _ in objectives)]

        def better_or_equal(a, b):           # a at least as good as b on every objective
            return all((value(a, m) <= value(b, m)) if d == "min" else (value(a, m) >= value(b, m))
                       for m, d in objectives)
        pareto = [r for r in feasible
                  if not any(o is not r and better_or_equal(o, r) and
                             any(value(o, m) != value(r, m) for m, _ in objectives)
                             for o in feasible)]
        first, direction = objectives[0]
        pareto.sort(key=lambda r: value(r, first) * (1 if direction == "min" else -1))
        remaining = len([c for c in candidates if key(c) not in seen])
        return {"done": remaining == 0, "evaluated": len(state["results"]),
                "remaining": remaining, "results": state["results"],
                "pareto": pareto, "state_file": state_file}

    def trade_study(self, name, candidates, regions=(), box=None, resolution_deg=2.0,
                    sensor_half_angle_deg=30.0, budget_s=140.0, state_file=None,
                    plane_cost=2.0, mission="eo", min_elevation_deg=10.0):
        """A ready-made PRESET of :meth:`search` for Walker constellations over a
        region (imaging or comms). For anything else -- GEO slots, HEO, single
        satellites, ground networks, link or manoeuvre trades -- call
        :meth:`search` with your own build/score.

        Search constellation architectures and score each one in STK.

        ``candidates``  list of dicts (see :meth:`candidate_grid`): altitude_km,
                        inclination (deg or "sso"), total_sats, planes, phase_f,
                        half_angle_deg (None = ``sensor_half_angle_deg``).
        ``regions``     area-target paths (e.g. from new_area_target_from_country)
                        or ``box`` = (lat_min, lat_max, lon_min, lon_max).

        Per candidate (all from STK, over the scenario period):
          coverage_pct        share of grid points seen at least once
          worst_revisit_h     largest per-point maximum gap (Revisit Time FOM)
          mean_revisit_h      grid average of the per-point maximum gaps
          time_covered_pct    mean share of the period a point is seen
          cost                total_sats + plane_cost * planes (simple proxy:
                              each plane ~ one launch; change plane_cost)
        ``mission``
          "eo"     imaging: a nadir camera of ``half_angle_deg``; the score is
                   the WORST REVISIT (largest gap between looks).
          "comms"  communications: each satellite serves everyone who sees it
                   above ``min_elevation_deg`` (the beam is sized exactly to
                   that elevation: sin(eta) = R cos(el)/(R+h)). Scores:
                   worst_point_time_pct  share of time the WORST grid point
                                         has at least one satellite in view
                                         (100 = continuous service)
                   worst_outage_h        longest gap with no satellite in view
                   The score is 100 - worst_point_time_pct (lower is better).
          Redundancy (>= 2 in view), link margins and gateways are checked on
          the chosen design afterwards with the comms helpers.

        Returns {"done", "evaluated", "remaining", "results" (sorted by cost),
        "pareto" (non-dominated in cost vs worst revisit, full coverage only),
        "state_file"}. Call again with the same ``state_file`` until done.
        Each result also carries ``orbit``: altitude, sma, e, inclination and
        the Walker geometry (RAAN spacing, in-plane spacing, phase offset).

        Revisit is a WORST GAP and includes the period edges: use >= 3 days
        for meaningful numbers. MEASURED (check run, 550 km SSO, Europe, 2 d):
        worst revisit is NOT monotonic in satellite count -- 6 sats/2 planes
        (23.7 h) was worse than 4 sats/2 planes (23.1 h), 6 planes worse than
        2 -- because Walker Delta spreads SSO planes over all local times and
        the phasing changes with T. Search phasing (phase_f) and plane count,
        not only the total; never assume more satellites is better.

        Search coarse-to-fine, not on a fixed grid: seed altitudes with
        :meth:`repeat_ground_track_altitudes` (exact, analytical), run, then
        :meth:`refine_candidates` around the best at 50 -> 10 -> 2 -> 0.5 km
        steps with all phasings, until the best design stops improving. Candidates that miss part of the region get
        worst_revisit_h = None and never enter the Pareto front.
        """
        import json, time, math
        started = time.time()
        state_file = state_file or os.path.join(self.output_dir(), "trade_%s.json" % name)
        state = {"results": []}
        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        key = lambda c: json.dumps(c, sort_keys=True)
        done_keys = {key(r["candidate"]) for r in state["results"]}
        todo = [c for c in candidates if key(c) not in done_keys]

        cov = "*/CoverageDefinition/TS_%s" % name
        if not self.exists(cov):
            if regions:
                self.new_coverage("TS_%s" % name, regions=list(regions),
                                  resolution_deg=resolution_deg, compute=False)
            elif box:
                self.new_coverage("TS_%s" % name, *box, resolution_deg=resolution_deg, compute=False)
            else:
                raise StkError("give regions (area targets) or a lat/lon box")
            for fom in ("Revisit Time", "Simple Coverage", "Coverage Time"):
                self.add_figure_of_merit(cov, fom.replace(" ", ""), fom)
        cov_obj = self.obj(cov)

        for idx, cand in enumerate(todo):
            if time.time() - started > float(budget_s):
                break
            alt = cand["altitude_km"]
            inc = self.sso_inclination_deg(alt) if str(cand.get("inclination")).lower() == "sso" \
                else float(cand["inclination"])
            if str(mission).lower() == "comms":
                half = round(math.degrees(math.asin(
                    EARTH_RADIUS_KM * math.cos(math.radians(float(min_elevation_deg)))
                    / (EARTH_RADIUS_KM + alt))), 4)
            else:
                half = cand.get("half_angle_deg") or sensor_half_angle_deg
            tag = "TS%d" % (len(state["results"]) + 1)
            T, P, F = int(cand["total_sats"]), int(cand["planes"]), int(cand["phase_f"])
            row = {"candidate": cand, "inclination_deg": round(inc, 3),
                   # the design itself, as a reader wants it: orbit + Walker geometry
                   "orbit": {"altitude_km": alt, "sma_km": round(altitude_to_sma(alt), 3),
                             "eccentricity": 0.0, "inclination_deg": round(inc, 3),
                             "raan_spacing_deg": round(360.0 / P, 3),
                             "in_plane_spacing_deg": round(360.0 / (T // P), 3),
                             "phase_offset_deg": round(F * 360.0 / T, 3),
                             "sensor_half_angle_deg": half,
                             "walker": "%d/%d/%d" % (T, P, F)}}
            members, constellation = [], None
            try:
                seed = self.new_satellite(tag, altitude_to_sma(alt), inc, 0.0,
                                          propagator="J2Perturbation")
                sensor = self.add_sensor_conic(seed, "Cam", half)
                self.point_nadir(sensor)
                members, constellation = self.walker_constellation(
                    seed, "Delta", num_planes=cand["planes"], total_sats=cand["total_sats"],
                    phase_f=cand["phase_f"], constellation_name=tag + "_C")
                try:
                    cov_obj.AssetList.RemoveAll()
                except Exception:
                    pass
                for m in members:
                    sp = (m + "/Sensor/Cam").lstrip("*/")
                    cov_obj.AssetList.Add(sp)
                cov_obj.ComputeAccesses()
                simple = self.fom_values(cov + "/FigureOfMerit/SimpleCoverage")
                revisit = self.fom_values(cov + "/FigureOfMerit/RevisitTime")
                ctime = self.fom_values(cov + "/FigureOfMerit/CoverageTime")
                coverage = 100.0 * float(simple.get("avg", 0.0))
                full = coverage >= 99.999
                row.update({
                    "coverage_pct": round(coverage, 2),
                    "worst_revisit_h": round(revisit["max"] / 3600.0, 2) if full else None,
                    "mean_revisit_h": round(revisit["avg"] / 3600.0, 2) if full else None,
                    "time_covered_pct": round(float(ctime.get("avg", 0.0)), 3),
                    "cost": cand["total_sats"] + float(plane_cost) * cand["planes"]})
                if str(mission).lower() == "comms":
                    worst_pt = float(ctime.get("min", 0.0))
                    row.update({"worst_point_time_pct": round(worst_pt, 3),
                                "worst_outage_h": row["worst_revisit_h"],
                                "min_elevation_deg": float(min_elevation_deg),
                                "score": round(100.0 - worst_pt, 3) if full else None})
                else:
                    row["score"] = row["worst_revisit_h"]
            except Exception as exc:
                row["error"] = str(exc)[:300]
            finally:
                for m in members:
                    self.cmd("Unload / %s" % m, label="remove candidate", fatal=False)
                if constellation:
                    self.cmd("Unload / %s" % constellation, label="remove candidate", fatal=False)
                if self.exists("*/Satellite/%s" % tag):
                    self.cmd("Unload / */Satellite/%s" % tag, label="remove seed", fatal=False)
            state["results"].append(row)
            with open(state_file, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=1)
            print("[trade] %s %s -> coverage %s%%, worst revisit %s h, cost %s%s"
                  % (tag, cand, row.get("coverage_pct"), row.get("worst_revisit_h"),
                     row.get("cost"), (" ERROR " + row["error"]) if "error" in row else ""))

        ok = [r for r in state["results"] if r.get("score") is not None]
        ok.sort(key=lambda r: (r["cost"], r["score"]))
        pareto, best = [], math.inf
        for r in ok:
            if r["score"] < best - 1e-9:
                pareto.append(r)
                best = r["score"]
        remaining = len([c for c in candidates
                         if key(c) not in {key(r["candidate"]) for r in state["results"]}])
        return {"done": remaining == 0, "evaluated": len(state["results"]),
                "remaining": remaining, "state_file": state_file,
                "results": sorted(state["results"], key=lambda r: (r.get("cost") or 1e9)),
                "pareto": pareto}

    def fom_values(self, fom_path):
        """Grid-wide statistics of a figure of merit, straight from STK.

        Returns {"min","max","avg","std","sum"} from the "Overall Value" data
        provider (one number per grid point, summarised).

        MEASURED (jobs 0126-0127, 9-point grid, one 550 km SSO camera, 1 day),
        checked against a plain access from a facility on a grid point:
          * Coverage Time default compute = PERCENT of the period covered,
            not seconds (grid 0.23-0.35 %; truth 279 s = 0.323 %)
          * Revisit Time default compute = MAXIMUM gap, in seconds
            (grid 47,413-47,545 s; truth gap between the two passes 47,451 s)
          * Number Of Accesses = 2 per point (truth: 2 passes)
          * Simple Coverage = 1 per point if covered at all
        Always state the compute type with the number.
        """
        r = self.read_data(fom_path, "Overall Value")
        if r.empty:
            raise StkError("%s has no values -- was ComputeAccesses run? %s"
                           % (fom_path, r.message))
        keys = (("Minimum", "min"), ("Maximum", "max"), ("Average", "avg"),
                ("Standard Deviation", "std"), ("Sum", "sum"))
        return {k: r.column(c)[0] for c, k in keys if c in r.columns}

    def figure_of_merit_report(self, fom_path, style=None, time_step_sec=None,
                               display=False):
        """Export (or open) the numbers a figure of merit produced.

        Revisit and coverage results live on the coverage definition and its
        figure of merit, not on the satellites, and the style names differ
        per object type. With ``style`` omitted this picks the first
        available style that looks like a per-grid-point value, so a caller
        does not have to know the catalogue.
        """
        available = self.report_styles(self._object_type(fom_path))
        if style is None:
            preferred = ("value by grid point", "value by point", "grid point",
                         "value", "statistics")
            for wanted in preferred:
                matches = [s for s in available if wanted in s.lower()]
                if matches:
                    style = matches[0]
                    break
            if style is None and available:
                style = available[0]
            if style is None:
                raise StkError(
                    "No report styles found for %s. List them with "
                    "session.report_styles(%r)."
                    % (fom_path, self._object_type(fom_path)))
            print("[info] figure-of-merit style not given; using %r "
                  "(available: %s)" % (style, ", ".join(available[:6])))
        if display:
            return self.open_report(fom_path, style)
        return self.export_report(fom_path, style,
                                  time_step_sec=time_step_sec or 3600)

    def chain_report(self, chain_path, style=None, display=True,
                     time_step_sec=None):
        """Report a chain's results, discovering a workable style.

        Chain style names are not guessable -- "Complete Chain" is rejected;
        the real name is "Complete Chain Access". With ``style`` omitted this
        picks the first available style that reports access.
        """
        available = self.report_styles(self._object_type(chain_path))
        if style is None:
            for wanted in ("complete chain access", "complete chain", "access",
                           "strand access", "link information"):
                matches = [s for s in available if wanted in s.lower()]
                if matches:
                    style = matches[0]
                    break
            if style is None and available:
                style = available[0]
            if style is None:
                raise StkError("No Chain report styles found. List them with "
                               'session.report_styles("Chain").')
            print("[info] chain style not given; using %r (available: %s)"
                  % (style, ", ".join(available[:6])))
        if display:
            return self.open_report(chain_path, style)
        return self.export_report(chain_path, style,
                                  time_step_sec=time_step_sec or 60)

    def open_access_report(self, from_path, to_path):
        """Open STK's access report window for one object pair."""
        self.cmd('ReportCreate %s Type Display Style "Access" AccessObject %s'
                 % (from_path, to_path), label="access report")
        return True

    # -- analysis -----------------------------------------------------------
    def compute_access(self, from_path, to_path):
        """Compute access and return ``(count, total_seconds, intervals)``.

        ``intervals`` is a list of ``(start_utcg, stop_utcg)`` string pairs.
        """
        try:
            access = self.obj(from_path).GetAccessToObject(self.obj(to_path))
            access.ComputeAccess()
            computed = access.ComputedAccessIntervalTimes
            intervals, total = [], 0.0
            for i in range(computed.Count):
                start, stop = computed.GetInterval(i)
                intervals.append((str(start), str(stop)))
                # ConvertDate returns a STRING; subtracting them raises
                # "unsupported operand type(s) for -: 'str' and 'str'".
                total += (float(self.root.ConversionUtility.ConvertDate("UTCG", "EpSec", stop))
                          - float(self.root.ConversionUtility.ConvertDate("UTCG", "EpSec", start)))
            return len(intervals), total, intervals
        except pywintypes.com_error as exc:
            raise StkError("Access computation %s -> %s failed: %s"
                           % (from_path, to_path, exc))

    def access_summary(self, from_paths, to_path):
        """Return ``[(name, count, total_seconds), ...]`` for several objects.

        A failure on one pair is reported and recorded as ``(name, None,
        None)`` rather than aborting the whole summary.
        """
        rows = []
        for path in from_paths:
            name = path.rsplit("/", 1)[-1]
            try:
                count, total, _ = self.compute_access(path, to_path)
                rows.append((name, count, total))
            except StkError as exc:
                sys.stderr.write("[warn] %s\n" % exc)
                rows.append((name, None, None))
        return rows

    def write_access_csv(self, rows, to_name, out_dir=None, filename=None):
        """Write an access summary to CSV. Returns the file path.

        The default filename includes ``to_name``, so calling this once per
        ground station produces one file per station. It used to default to a
        fixed "access_summary.csv", which silently overwrote itself on every
        call -- a script reporting three stations kept only the last, while
        printing that it had written three.

        Pass ``filename`` explicitly to combine several targets in one file.
        """
        if not filename:
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(to_name))
            filename = "access_%s.csv" % (safe or "target")
        out_file = os.path.join(self.output_dir(out_dir), filename)
        if os.path.exists(out_file):
            sys.stderr.write("[warn] overwriting %s\n" % out_file)
        with open(out_file, "w") as handle:
            handle.write("Object,Target,NumPasses,TotalDuration_s\n")
            for name, count, total in rows:
                if count is None:
                    handle.write("%s,%s,ERROR,ERROR\n" % (name, to_name))
                else:
                    handle.write("%s,%s,%d,%.3f\n" % (name, to_name, count, total))
        print("[file] %s" % out_file)
        return out_file