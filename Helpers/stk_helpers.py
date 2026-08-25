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
import os
import re as _re
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
        for source in (self.root, self.app):
            try:
                type_info = source._oleobj_.GetTypeInfo()
                typelib, _index = type_info.GetContainingTypeLib()
            except Exception:
                continue
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

    def cmd(self, command, label="", fatal=True):
        """Execute one Connect command.

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

    def obj(self, path):
        """Return the object-model interface for an STK path, or raise."""
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
        resolved = out_dir or os.path.join(
            os.path.dirname(os.path.abspath(sys.argv[0] or __file__)), "stk_output")
        os.makedirs(resolved, exist_ok=True)
        return resolved

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

    # -- satellites ---------------------------------------------------------
    def new_satellite(self, name, sma_km, inc_deg, raan_deg,
                      ecc=0.0, arg_perigee_deg=0.0, true_anomaly_deg=0.0,
                      propagator="J2Perturbation", start=None, stop=None,
                      step_sec=60.0, coord_system="J2000", epoch=None,
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

        if central_body:
            # Connect's New command takes an optional CentralBody keyword.
            # Fall back to a plain New (scenario central body) if the
            # installed STK build rejects it, so the script still runs.
            created = self.cmd("New / */Satellite %s CentralBody %s" % (actual, central_body),
                               label="create satellite on %s" % central_body, fatal=False)
            if not created:
                self.cmd("New / */Satellite %s" % actual, label="create satellite")
                sys.stderr.write(
                    "[warn] central body '%s' not applied to %s; satellite uses the "
                    "scenario central body.\n" % (central_body, actual))
        else:
            self.cmd("New / */Satellite %s" % actual, label="create satellite")

        self._set_classical_state(
            path, propagator=propagator, start=start, stop=stop,
            step_sec=step_sec, coord_system=coord_system, epoch=epoch,
            sma_km=sma_km, ecc=ecc, inc_deg=inc_deg,
            arg_perigee_deg=arg_perigee_deg, raan_deg=raan_deg,
            true_anomaly_deg=true_anomaly_deg)
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
            # Circular-ish sanity band: radius must be within 20% of the
            # requested semi-major axis. A unit error is off by ~1000x.
            if abs(radius - float(sma_km)) > 0.2 * float(sma_km):
                attempts.append(
                    "    [%s] accepted but orbit radius read back as %.1f km, "
                    "expected ~%.1f km (unit mismatch)" % (name, radius, sma_km))
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
                return None
            x = float(datasets.GetDataSetByName("x").GetValues()[0])
            y = float(datasets.GetDataSetByName("y").GetValues()[0])
            z = float(datasets.GetDataSetByName("z").GetValues()[0])
        except Exception:
            return None
        return (x * x + y * y + z * z) ** 0.5

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
        """Point a sensor at the ground.

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

    # -- ground assets ------------------------------------------------------
    def new_facility(self, name, lat_deg, lon_deg, alt_km=0.0, central_body=None):
        """Create a ground facility at a geodetic position. Altitude in km."""
        path = self.unique_path("*/Facility/%s" % name)
        actual = path.rsplit("/", 1)[-1]
        if central_body:
            created = self.cmd("New / */Facility %s CentralBody %s" % (actual, central_body),
                               label="create facility on %s" % central_body, fatal=False)
            if not created:
                self.cmd("New / */Facility %s" % actual, label="create facility")
        else:
            self.cmd("New / */Facility %s" % actual, label="create facility")
        self.cmd("SetPosition %s Geodetic %.9f %.9f %.9f"
                 % (path, float(lat_deg), float(lon_deg), float(alt_km)),
                 label="facility position")
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
        lowered = style.lower()
        near = [s for s in available if lowered in s.lower() or s.lower() in lowered]
        raise StkError(
            "'%s' is not a %s report style in this STK installation.%s\n"
            "  %d styles are available; list them with "
            "session.report_styles(%r)."
            % (style, object_type,
               ("\n  Did you mean: %s?" % ", ".join(near[:4])) if near else "",
               len(available), object_type))

    def open_report(self, path, style):
        """Open one of STK's own report windows on an object."""
        self._check_style(path, style)
        self.cmd('ReportCreate %s Type Display Style "%s"' % (path, style),
                 label="open report '%s'" % style)
        return style

    def open_graph(self, path, style):
        """Open one of STK's own graph windows on an object.

        Same style names as reports -- STK draws the style's data as a graph
        rather than a table.
        """
        self._check_style(path, style)
        self.cmd('GraphCreate %s Type Display Style "%s"' % (path, style),
                 label="open graph '%s'" % style)
        return style

    def export_report(self, path, style, filename=None, time_step_sec=60,
                      out_dir=None):
        """Write one of STK's own reports to a file. Returns the file path."""
        self._check_style(path, style)
        if not filename:
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in style)
            filename = "%s_%s.csv" % (path.rsplit("/", 1)[-1], safe)
        out_file = os.path.join(self.output_dir(out_dir), filename)
        self.cmd('ReportCreate %s Type Export Style "%s" File "%s" TimeStep %g'
                 % (path, style, out_file, float(time_step_sec)),
                 label="export report '%s'" % style)
        print("[file] %s" % out_file)
        return out_file

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