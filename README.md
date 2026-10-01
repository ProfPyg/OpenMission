# Pronoia — Foresee your mission

Describe a space mission in plain words; Pronoia's agent plans it with you and
builds it in **your own running STK** (Windows), step by step, after you
approve the plan.

## Run

```cmd
venv\Scripts\activate.bat
pip install -r requirements.txt -r Helpers\requirements-stk.txt
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

- [ ] Chat send arrow in sand colour (noted, not done yet)
