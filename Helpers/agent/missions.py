"""missions.py -- each mission (chat, steps, costs, outputs) as one JSON file."""

from __future__ import annotations

import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "missions")


def mission_dir(mission_id):
    path = os.path.join(ROOT, mission_id)
    os.makedirs(os.path.join(path, "data"), exist_ok=True)
    return path


def new_id(title):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")[:40] or "Mission"
    return "%s_%s" % (time.strftime("%Y%m%d_%H%M%S"), slug)


def save(mission_id, state):
    with open(os.path.join(mission_dir(mission_id), "mission.json"), "w",
              encoding="utf-8") as handle:
        json.dump(state, handle, indent=1, default=str)


def load(mission_id):
    with open(os.path.join(mission_dir(mission_id), "mission.json"), "r",
              encoding="utf-8") as handle:
        return json.load(handle)


def list_missions():
    if not os.path.isdir(ROOT):
        return []
    out = []
    for name in sorted(os.listdir(ROOT), reverse=True):
        path = os.path.join(ROOT, name, "mission.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    meta = json.load(handle)
                out.append((name, meta.get("title", name), meta.get("usd", 0.0)))
            except Exception:
                pass
    return out


def delete(mission_id):
    """Remove a mission folder (chat, data, outputs). Only inside missions/."""
    import shutil
    path = os.path.realpath(os.path.join(ROOT, mission_id))
    if os.path.dirname(path) != os.path.realpath(ROOT) or mission_id.startswith("_"):
        raise ValueError("not a mission: %r" % mission_id)
    shutil.rmtree(path, ignore_errors=True)
