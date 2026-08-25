"""
check_prompt_api.py -- keep prompts/stk_agent.md honest about stk_helpers.py.

The agent writes scripts from the prompt. If the prompt documents a function
or keyword argument that stk_helpers.py does not actually have, EVERY
generated script that uses it fails with "unexpected keyword argument" or
AttributeError -- and the user, not the developer, discovers it.

This has already happened three times:

    new_walker(raan_deg=...)          -> collided with a derived argument
    add_sensor_conic(angular_res_deg) -> real name is angular_resolution_deg
    add_sensor_rectangular(half_across_deg) -> real name is half_angle_across_deg

So the prompt is now checked against the module mechanically. Run it after
editing either file:

    python check_prompt_api.py

Exit code 0 = the prompt only describes things that exist. It needs no STK
and no Windows -- the COM layer is stubbed -- so it can run in CI.
"""

from __future__ import annotations

import inspect
import io
import os
import re
import sys
import types


def _stub_com():
    """Stub pywin32 so stk_helpers imports anywhere, including Linux CI."""
    win32com = types.ModuleType("win32com")
    client = types.ModuleType("win32com.client")
    client.Dispatch = client.GetActiveObject = lambda *a, **k: None
    win32com.client = client
    sys.modules.setdefault("win32com", win32com)
    sys.modules.setdefault("win32com.client", client)
    pywintypes = types.ModuleType("pywintypes")
    pywintypes.com_error = Exception
    sys.modules.setdefault("pywintypes", pywintypes)
    pythoncom = types.ModuleType("pythoncom")
    pythoncom.TKIND_ENUM = 2
    sys.modules.setdefault("pythoncom", pythoncom)


def _iter_calls(text):
    """Yield (function_name, argument_text) for session./stk. calls."""
    for match in re.finditer(r"\b(?:session|stk)\.([a-zA-Z_]+)\(", text):
        name, index, depth = match.group(1), match.end(), 1
        while index < len(text) and depth:
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
            index += 1
        yield name, text[match.end():index - 1]


def check(prompt_path, module):
    prompt = io.open(prompt_path, encoding="utf-8").read()
    problems = []

    for name, args in _iter_calls(prompt):
        owner = module.StkSession if hasattr(module.StkSession, name) else module
        func = getattr(owner, name, None)
        if func is None:
            problems.append("%s() is documented but does not exist" % name)
            continue
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            continue
        params = set(signature.parameters)
        aliases = set(getattr(func, "_aliases", {}))
        takes_kwargs = any(p.kind == p.VAR_KEYWORD
                           for p in signature.parameters.values())

        for keyword in re.findall(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*=", args):
            if keyword in params or keyword in aliases or takes_kwargs:
                continue
            problems.append(
                "%s(%s=...) is documented but is not a parameter; real ones: %s"
                % (name, keyword, ", ".join(sorted(p for p in params if p != "self"))))

    return problems


def main():
    _stub_com()
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import stk_helpers

    prompt_path = os.path.join(os.path.dirname(here), "prompts", "stk_agent.md")
    if not os.path.isfile(prompt_path):
        print("prompt not found at %s" % prompt_path)
        return 2

    problems = check(prompt_path, stk_helpers)
    if problems:
        print("PROMPT/API MISMATCHES (%d) -- fix the prompt or the module:" % len(problems))
        for problem in problems:
            print("  * %s" % problem)
        return 1

    print("OK: every function and keyword the prompt documents exists in "
          "stk_helpers.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
