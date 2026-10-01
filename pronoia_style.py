"""pronoia_style.py -- the Pronoia look: Earth palette, centred landing, video background.

Kept apart from styles.py (the older OpenMission theme used by the other
pages) so the two can coexist while the rest of the app is migrated.
"""

PALETTE = {"bg": "#0b0d0c", "ink": "#ece6d8", "mute": "#a39d8e", "dim": "#6e695e",
           "line": "#2a2a24", "olive": "#8f9a5c", "sand": "#d9c9a3", "clay": "#b08a5a"}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap');
:root{--bg:#0b0d0c;--ink:#ece6d8;--mute:#a39d8e;--dim:#6e695e;--line:#2a2a24;
      --olive:#8f9a5c;--sand:#d9c9a3;--clay:#b08a5a;--panel:rgba(20,20,17,.78)}
html,body,.stApp{background:var(--bg)!important;color:var(--ink);font-family:Inter,"Segoe UI",system-ui,sans-serif}
[data-testid="stAppViewContainer"],[data-testid="stMain"],.main{background:transparent!important}
[data-testid="stHeader"]{background:transparent!important}
[data-testid="stToolbar"],[data-testid="stDecoration"],#MainMenu,footer{display:none!important}
.block-container{padding-top:1.2rem!important;max-width:1500px!important;position:relative;z-index:2}
h1,h2,h3,h4,p,li,label,span,div{font-family:Inter,"Segoe UI",system-ui,sans-serif}
a{color:var(--sand)}

/* background video */
.pr-bg{position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden}
.pr-bg video,.pr-bg img{width:100%;height:100%;object-fit:cover;filter:blur(var(--blur,6px)) sepia(.35) saturate(.8) brightness(.8);transform:scale(1.04)}
.pr-bg .shade{position:absolute;inset:0;background:radial-gradient(ellipse at 50% 38%,rgba(11,13,12,.30),rgba(11,13,12,.93) 80%)}

/* hide Streamlit's page list: Pronoia has its own navigation */
[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"],[data-testid="stSidebarNav"]{display:none!important}
.pr-dlg{font-size:14px;color:var(--mute);line-height:1.6;margin-bottom:14px}
.pr-dlg b{color:var(--sand);font-weight:500}
.pr-dlg-small{font-size:12px;letter-spacing:.04em;color:var(--dim);margin-top:8px}
.st-key-examples [data-testid="stButtonGroup"],.st-key-examples [data-testid="stButtonGroup"] > div{justify-content:center!important;display:flex;flex-wrap:wrap}
[data-testid="stDialog"] h2,[data-testid="stDialog"] [data-testid="stHeading"] *{font-weight:400!important;font-size:20px!important}
.pr-exlabel{font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:var(--dim);text-align:center;margin:18px 0 6px}
[data-testid="stDialog"] [role="dialog"]{background:#141410!important;border:1px solid #37352c;border-radius:16px}
.st-key-examples [data-testid="stButtonGroup"] button{border:1px solid #333128!important;background:rgba(20,20,17,.5)!important;color:var(--mute)!important;border-radius:99px!important}

/* top bar */
.st-key-logo{position:relative}
.pr-logo-svg svg{height:34px;width:auto;display:block}
.st-key-logo [data-testid="stElementContainer"]:has(button),.st-key-logo .element-container:has(button){position:absolute!important;top:0!important;left:0!important;right:0!important;bottom:0!important;height:100%!important;margin:0!important;z-index:5}
.st-key-logo [data-testid="stButton"],.st-key-logo [data-testid="stButton"] > *{height:100%!important;width:100%!important;min-height:0!important}
.st-key-logo [data-testid="stButton"] button{width:100%;height:100%;opacity:0!important;cursor:pointer}
.st-key-promptbox .stTextInput input{background:transparent!important;border:1px dashed #4a4638!important;border-radius:99px!important;font-size:13px!important;padding:6px 14px!important;color:var(--ink)!important}
.pr-logo{letter-spacing:.34em;font-weight:500;font-size:17px;color:var(--ink);padding-top:6px}
.pr-status{font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);text-align:right;padding-top:9px}
.pr-status i{display:inline-block;width:22px;height:1px;background:var(--olive);vertical-align:middle;margin-right:10px}
.pr-status.off i{background:var(--clay)}
.pr-rule{border-bottom:1px solid var(--line);margin:10px -4rem 0}

/* landing */
.pr-eyebrow{font-size:12px;letter-spacing:.22em;color:var(--sand);text-transform:uppercase;text-align:center;margin-top:7vh}
.pr-h1{font-size:60px;font-weight:300;letter-spacing:-.02em;line-height:1.1;text-align:center;margin:18px 0 16px;color:var(--ink)}
.pr-h1 em{font-style:normal;color:var(--sand)}
.pr-sub{color:var(--mute);font-size:17px;text-align:center;letter-spacing:.02em;margin-bottom:34px}
.pr-sub span{color:var(--dim);margin:0 10px}
.pr-foot{text-align:center;font-size:11px;letter-spacing:.14em;color:var(--dim);margin-top:9vh}
.st-key-promptbox{background:var(--panel);border:1px solid #37352c!important;border-radius:18px!important;padding:14px 16px!important}
.st-key-promptbox textarea{background:transparent!important;border:none!important;color:var(--ink)!important;font-size:16px!important;box-shadow:none!important}
.st-key-promptbox [data-baseweb="textarea"]{background:transparent!important;border:none!important}
.st-key-promptbox [data-testid="stPopover"] button{border:1px dashed #4a4638!important;border-radius:99px!important;background:transparent!important;color:var(--mute)!important;font-size:13px!important;padding:4px 12px!important;min-height:0!important}
.st-key-examples button{border:1px solid #333128!important;border-radius:99px!important;background:rgba(20,20,17,.5)!important;color:var(--mute)!important;font-size:13px!important;padding:4px 12px!important;min-height:0!important}
.st-key-examples [data-testid="stHorizontalBlock"]{justify-content:center}

/* buttons */
button[kind="primary"],[data-testid="stBaseButton-primary"]{background:var(--sand)!important;color:#15140f!important;border:none!important;border-radius:99px!important;font-weight:600!important;padding:.55rem 1.4rem!important}
button[kind="primary"] p{white-space:nowrap}
button[kind="primary"]:hover{background:#e6d9b8!important}
button[kind="secondary"],[data-testid="stBaseButton-secondary"]{background:transparent!important;border:1px solid #4a4638!important;color:var(--ink)!important;border-radius:99px!important}
[data-testid="stPopover"] > div > button{background:transparent!important;border:none!important;color:var(--mute)!important;font-size:14px!important}

/* inputs */
.stTextArea textarea,.stTextInput input{background:rgba(20,20,17,.8)!important;color:var(--ink)!important;border-color:#37352c!important}

/* results show/hide */
.st-key-outtoggle{align-items:flex-end}
.st-key-outtoggle button{background:transparent!important;border:none!important;padding:0!important;min-height:0!important;box-shadow:none!important}
.st-key-outtoggle button p{font-size:11px!important;letter-spacing:.14em;text-transform:uppercase;color:var(--mute)!important}
.st-key-outtoggle button:hover p{color:var(--sand)!important}
/* mission rail */
.st-key-rail{gap:10px}
.st-key-rail button{width:38px!important;height:38px!important;min-height:0!important;padding:0!important;border-radius:10px!important;font-size:11px!important;letter-spacing:.06em}
.st-key-rail button[kind="primary"]{background:transparent!important;border:1px solid var(--sand)!important;color:var(--sand)!important}
.st-key-rail button p{color:inherit!important}
/* mission list */
.st-key-mlist button{justify-content:space-between!important;width:100%;border:none!important;border-radius:8px!important;background:transparent!important;color:var(--mute)!important;text-align:left}
.st-key-mlist button p{width:100%;text-align:left}
.st-key-mlist button:hover{background:#1c1c18!important;color:var(--ink)!important}
.st-key-newm button{width:100%;border-radius:10px!important}

/* mission header + tabs */
.pr-title{font-size:30px;font-weight:300;color:var(--ink);margin-top:6px}
.pr-meta{font-size:11.5px;letter-spacing:.07em;color:var(--dim);text-transform:uppercase;margin-top:6px}
.pr-meta b{color:var(--olive);font-weight:500}
.stTabs [data-baseweb="tab-list"]{gap:28px;border-bottom:1px solid var(--line)}
.stTabs [data-baseweb="tab"]{background:transparent!important;color:var(--mute);padding:10px 2px}
.stTabs [aria-selected="true"]{color:var(--ink)!important}
.stTabs [data-baseweb="tab-highlight"]{background:var(--sand)!important;height:1px}
.stTabs [data-baseweb="tab-border"]{display:none}

/* results readout */
.pr-q{font-size:11px;letter-spacing:.2em;color:var(--olive);text-transform:uppercase;margin:6px 0 10px}
.pr-concl{font-size:18px;line-height:1.6;font-weight:300;color:var(--ink);margin-bottom:22px}
.pr-concl b{font-weight:500;color:var(--sand)}
.pr-read{border-top:1px solid var(--line);margin-bottom:26px}
.pr-r{display:flex;justify-content:space-between;align-items:baseline;padding:12px 0;border-bottom:1px solid var(--line);gap:20px}
.pr-r .l{font-size:14.5px;color:var(--ink)}
.pr-r .l small{display:block;font-size:11px;color:var(--mute);margin-top:3px}
.pr-r .v{font-size:30px;font-weight:300;white-space:nowrap;font-variant-numeric:tabular-nums;color:#f6f1e6}
.pr-r .v.long{font-size:15px;white-space:normal;text-align:right;max-width:62%;line-height:1.45}
.pr-r .v u{text-decoration:none;font-size:14px;color:var(--mute);margin-left:4px}
.pr-notes-h{margin-top:30px;color:var(--dim)!important}
.pr-notes{font-size:12.5px;line-height:1.65;color:var(--dim);border-left:1px solid #2a2a24;padding-left:14px}
.pr-notes > div{margin-bottom:9px}
.pr-notes span{font-size:9.5px;letter-spacing:.16em;margin-right:8px;font-weight:600;opacity:.85}
.pr-notes .fixed{color:var(--clay)} .pr-notes .needs_user{color:#d9826b} .pr-notes .info{color:var(--dim)}

/* messages and errors in Pronoia's own style, never big coloured boxes */
[data-testid="stAlert"],[data-testid="stException"]{background:rgba(20,20,17,.85)!important;border:none!important;
  border-left:2px solid var(--clay)!important;border-radius:10px!important;color:var(--mute)!important}
[data-testid="stAlert"] *,[data-testid="stException"] *{color:var(--mute)!important;background:transparent!important;font-size:14px!important}
[data-testid="stAlert"] svg,[data-testid="stAlertDynamicIcon"]{display:none!important}
[data-testid="stException"] pre,[data-testid="stException"] code{font-size:12px!important;color:var(--dim)!important}
/* loader: the app icon */
.pr-loader{display:flex;align-items:center;gap:10px;color:var(--mute);font-size:13px;letter-spacing:.03em;padding:6px 0}
.pr-loader.big{flex-direction:column;justify-content:center;gap:14px;padding:9vh 0 7vh;font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--dim)}
/* chat + log */
[class*="st-key-u"]{background:rgba(28,27,22,.7);border-left:2px solid var(--sand);border-radius:10px;padding:10px 16px;margin:14px 0}
[class*="st-key-u"] p{color:var(--ink)!important}
.st-key-examples [data-testid="stButtonGroup"]{justify-content:center;display:flex}
.st-key-examples button{white-space:nowrap}
.st-key-promptbox [data-testid="stPopover"] button p{white-space:nowrap}
.st-key-promptbox [data-testid="stPopover"]{margin-right:8px}
[data-testid="stChatMessage"]{background:transparent!important}
.pr-now{font-size:13px;color:var(--mute);letter-spacing:.03em;padding:8px 0}
.pr-now i{display:inline-block;width:22px;height:1px;background:var(--olive);vertical-align:middle;margin-right:10px}
.pr-logline{font-size:13px;color:var(--mute);padding:3px 0;border-bottom:1px solid #1d1d19}
.pr-logline span{display:inline-block;width:64px;font-size:10.5px;letter-spacing:.12em}
.pr-plan{background:var(--panel);border:1px solid #37352c;border-radius:14px;padding:18px 22px;margin:10px 0}
[data-testid="stExpander"]{border:1px solid var(--line)!important;border-radius:10px!important;background:rgba(14,14,12,.75)}
[data-testid="stSidebar"]{background:#0e0e0c!important;border-right:1px solid var(--line)}
</style>
"""


def background(opacity=0.7, blur=6, motion=True):
    """Fixed full-screen background: the video (or its still) under a dark shade."""
    media = ('<video autoplay muted loop playsinline poster="app/static/bg.jpg">'
             '<source src="app/static/bg.webm" type="video/webm"></video>'
             if motion else '<img src="app/static/bg.jpg">')
    return ('<div class="pr-bg" style="opacity:%.2f;--blur:%dpx">%s<div class="shade"></div></div>'
            % (opacity, blur, media))


_ICON = """<svg viewBox="0 0 160 160" width="{s}" height="{s}" xmlns="http://www.w3.org/2000/svg">
<ellipse cx="80" cy="80" rx="44" ry="29" fill="none" stroke="#b08a5a" stroke-width="2.4"/>
<circle cx="36" cy="80" r="17" fill="none" stroke="#8f9a5c" stroke-width="4"/>
<circle cx="124" cy="80" r="17" fill="none" stroke="#d9c9a3" stroke-width="4"/>
<circle r="5.5" fill="#d9c9a3"><animateMotion dur="{d}s" repeatCount="indefinite"
 path="M 36 80 A 44 29 0 1 1 124 80 A 44 29 0 1 1 36 80"/></circle></svg>"""


def loader(text="", big=False):
    """Pronoia's only loading signal: the app icon with its satellite moving."""
    if big:
        return ('<div class="pr-loader big">%s<div>%s</div></div>'
                % (_ICON.format(s=110, d=3.5), text))
    return ('<div class="pr-loader">%s<span>%s</span></div>' % (_ICON.format(s=34, d=3.5), text))
