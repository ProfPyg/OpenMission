# Pronoia — Foresee your mission

Describe a space mission in plain words; Pronoia's agent plans it with you and
builds it in **your own running STK** (Windows), step by step, after you
approve the plan.

## Run

```cmd
venv\Scripts\activate.bat
pip install -r requirements.txt
set ANTHROPIC_API_KEY=sk-ant-...
streamlit run app.py
```

Open STK before (or after — Settings › Re-check STK).

## Layout

| Path | What it is |
|---|---|
| `app.py` | Entry point (opens the Pronoia page) |
| `pages/stk_agent.py` | The Pronoia app page |
| `pronoia_style.py` | Look & feel (Earth palette, background video) |
| `prompts/stk_agent_live.md` | The agent's instructions |
| `Helpers/stk_helpers.py` | Verified STK automation library — the agent's "language" |
| `Helpers/agent/` | Agent engine: model loop, tools, STK link, charts, missions |
| `Helpers/bridge/` | Trainer: runs probe jobs in STK to teach the helpers (`watcher.py`) |
| `Helpers/verify_stk_helpers.py` | Smoke test of the helpers against a live STK |
| `assets/`, `static/` | Logo, icon, background video, planet textures (credits in `static/bodies/CREDITS.txt`) |
| `missions/` | Your saved missions (chat, steps, data, exported files) — not in git |
| `archive/pass_prediction/` | Stand-alone pass-prediction tool, kept outside the app |

- [x] Chat send arrow in sand colour
- [x] Bold text in the plan box in sand colour
- [x] Setup: add pywin32 to requirements (Windows-only marker) and simplify install steps for a new PC
- [x] Closing Pronoia stops the running STK step immediately (kill the step process, no orphan python.exe)
- [ ] (after UI migration) Page refresh keeps the live mission: reattach to the running run (live log, timer), never show a stale plan or allow a second Build it
- [ ] (after UI migration) Process log updates live while the agent runs
- [ ] Downloads: results (numbers as CSV/Excel, charts as PNG, a mission report PDF) and the STK scenario itself (saved .vdf package with all objects), plus the step scripts for full reproducibility
- [ ] Windows installer: one PronoiaSetup.exe with embedded Python + all libraries (Inno Setup), desktop icon, nothing installed by hand
- [ ] Charts readable: bigger fonts, thicker lines, target line (e.g. 6 h), best design highlighted
- [ ] Stop the agent's working notes leaking into chat
- [ ] Plan states the optimisation objective and tie-breakers
- [ ] After validation: UI to a desktop app (Tauri + React/Svelte); backend stays Python (FastAPI)
- [ ] Lifetime: bundle + auto-update CelesTrak space-weather file (SW-All.txt) so STK uses real solar flux
- [x] Stop button while the agent runs: stops the model loop and kills the running STK step at once; mission saved as "Stopped", user can resume, change the plan, or restart
