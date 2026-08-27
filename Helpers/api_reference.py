"""
api_reference.py -- generate the prompt's API section from the code itself.

The prompt used to contain a hand-typed list of what stk_helpers can do. That
list drifted from the code, and every drift became a bug in a generated
script: `angular_res_deg` instead of `angular_resolution_deg`,
`half_across_deg` instead of `half_angle_across_deg`, and -- worst --
new_facility documented without `central_body`, which put a lunar landing
site in the Arabian Sea. The description was wrong, so the agent was wrong.

Generating it removes the failure mode rather than policing it: nobody types
the description, so nobody can mistype it.

The module is parsed with `ast`, NOT imported: the Streamlit app runs on
Linux where pywin32 does not exist, so importing stk_helpers would fail.
Parsing also means no STK, no COM and no side effects.

    from api_reference import generate
    markdown = generate("Helpers/stk_helpers.py")
"""

from __future__ import annotations

import ast
import io

#: Grouping for readability. A method not listed here still appears, under
#: "Other", so a new helper is never silently left out of the prompt.
_GROUPS = (
    ("Connecting and scenarios",
     ("attach", "launch", "ensure_scenario", "set_time_period", "set_units",
      "rewind", "save_scenario", "close_scenario", "cmd", "obj", "exists",
      "unique_path", "enum", "find_enum", "has_enums", "directory",
      "output_dir", "announce_output_dir", "report_styles")),
    ("Satellites and orbits",
     ("new_satellite", "new_walker", "new_geo_satellite",
      "new_satellite_from_tle", "set_hpop_gravity", "propagate", "set_epoch",
      "orbit_radius_km", "subsatellite_point", "subsatellite_longitude_deg",
      "find_data_file")),
    ("Sensors and pointing",
     ("add_sensor_conic", "add_sensor_rectangular", "point_nadir",
      "point_az_el", "point_at_target")),
    ("RF",
     ("add_transmitter", "add_receiver")),
    ("Ground assets, regions, coverage, chains",
     ("new_facility", "new_planet", "new_area_target",
      "new_area_target_ellipse", "new_chain", "new_coverage",
      "add_figure_of_merit", "set_attitude_profile", "set_constraint")),
    ("Analysis",
     ("compute_access", "access_summary", "next_pass", "pass_details")),
    ("Delivering results",
     ("declare_deliverables", "publish_access", "publish_report",
      "open_report", "open_graph", "export_report", "open_access_report",
      "figure_of_merit_report", "write_access_csv", "write_pass_list_csv",
      "output_summary")),
)


def _signature(node: ast.FunctionDef) -> str:
    """Render a def's arguments the way a caller would write them."""
    args = node.args
    names = [a.arg for a in args.args if a.arg not in ("self", "cls")]
    defaults = list(args.defaults)
    # defaults align to the END of the positional list
    pad = len(names) - len(defaults)
    parts = []
    for index, name in enumerate(names):
        if index < pad:
            parts.append(name)
        else:
            default = defaults[index - pad]
            try:
                parts.append("%s=%s" % (name, ast.unparse(default)))
            except Exception:
                parts.append("%s=..." % name)
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    return "%s(%s)" % (node.name, ", ".join(parts))


def _summary(node: ast.FunctionDef, max_lines: int = 6) -> list[str]:
    """The docstring's opening paragraphs, trimmed for the prompt.

    Docstrings carry the reasoning behind measured behaviour -- that nadir is
    +90, that a TLE needs its span checked -- so they are worth including,
    but not in full.
    """
    doc = ast.get_docstring(node) or ""
    if not doc:
        return []
    out: list[str] = []
    for line in doc.strip().splitlines():
        stripped = line.strip()
        if not stripped and out:
            break                      # first blank line ends the summary
        if stripped:
            out.append(stripped)
        if len(out) >= max_lines:
            break
    return out


def generate(module_path: str, include_constants: bool = True) -> str:
    """Return a markdown API reference for stk_helpers.py."""
    tree = ast.parse(io.open(module_path, encoding="utf-8").read())

    methods: dict[str, ast.FunctionDef] = {}
    functions: dict[str, ast.FunctionDef] = {}
    constants: list[str] = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "StkSession":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                    methods[item.name] = item
        elif isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            functions[node.name] = node
        elif include_constants and isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper() \
                        and not target.id.startswith("_"):
                    try:
                        constants.append("%s = %s" % (target.id, ast.unparse(node.value)))
                    except Exception:
                        pass

    lines: list[str] = []
    lines.append("The helper module is `stk_helpers.py`. Its API below is")
    lines.append("generated from the module source, so it is always exactly what")
    lines.append("the code does. Call these and nothing else; anything starting")
    lines.append("with an underscore is internal and may change.")
    lines.append("")

    if functions:
        lines.append("### Module-level helpers (`stk.<name>`)")
        lines.append("")
        for name in sorted(functions):
            lines.append("    stk.%s" % _signature(functions[name]))
            for text in _summary(functions[name], max_lines=2):
                lines.append("        %s" % text)
        lines.append("")

    if constants:
        lines.append("### Constants (`stk.<NAME>`)")
        lines.append("")
        for constant in constants:
            lines.append("    stk.%s" % constant)
        lines.append("")

    covered: set[str] = set()
    for title, names in _GROUPS:
        present = [n for n in names if n in methods]
        if not present:
            continue
        covered.update(present)
        lines.append("### %s" % title)
        lines.append("")
        for name in present:
            node = methods[name]
            prefix = "stk.StkSession." if name in ("attach", "launch") else "session."
            lines.append("    %s%s" % (prefix, _signature(node)))
            for text in _summary(node):
                lines.append("        %s" % text)
            lines.append("")

    leftover = sorted(set(methods) - covered)
    if leftover:
        lines.append("### Other")
        lines.append("")
        for name in leftover:
            lines.append("    session.%s" % _signature(methods[name]))
            for text in _summary(methods[name]):
                lines.append("        %s" % text)
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    text = generate(os.path.join(here, "stk_helpers.py"))
    print(text)
