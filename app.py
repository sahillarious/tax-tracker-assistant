"""Streamlit UI. Run with:  streamlit run app.py

One screen, three panes:  inbox (left) | email with evidence (middle) | what I found + actions (right).
Nothing is written to the tracker unless a person clicks Approve.
"""
import html
import re
from email.utils import parseaddr

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import checks
import config
import demo_reset
import extractor
import live_email
import pipeline
import reply

st.set_page_config(page_title="Tax-season tracker assistant (DEMO)", layout="wide")

PANE_HEIGHT = 560   # height of each scrolling pane (fits a 1440x900 screen)
LIST_HEIGHT = 440   # inbox list is shorter: the filter + progress sit above it
KIND_COLORS = {"document": "#b3d4ff", "life change": "#b7efc5", "question": "#ffd9a0"}
FIELD_NAMES = {"docs_received": "Documents received",
               "docs_missing": "Still needed from client",
               "life_changes": "Life changes noted"}
# Plain-English card titles. The technical flag code is never shown to the user.
FLAG_TITLES = {
    "unknown_sender": "Sender is not in the tracker",
    "attachment_claimed_but_none": "Attachment missing",
    "document_without_attachment": "Document not attached",
    "unidentified_attachment": "Unclear attachment",
    "vague_documents": "Documents not named",
    "employer_conflict": "Employer changed",
    "no_rule_for_life_change": "No checklist for this life event",
    "client_question": "Client asked a question",
    "possible_prompt_injection": "Suspicious instructions in the email",
    "extraction_failed": "Could not read this email automatically",
    "empty_email": "Nothing to read",
}
SHORT = {"Needs review": "Review"}  # shorter label so the filter chips fit on one line
CHIP_CLASS = {"Ready": "chip-ready", "Needs review": "chip-review", "Can't process": "chip-cant",
              "Approved": "chip-approved", "Rejected": "chip-rejected"}
FILTERS = {"All": lambda s: True,
           "Needs review": lambda s: s in ("Needs review", "Can't process"),
           "Ready": lambda s: s == "Ready",
           "Done": lambda s: s in ("Approved", "Rejected")}

CSS = """
<style>
.block-container {padding-top: 3rem; padding-bottom: 0.5rem; max-width: 100%;}
.app-title {font-size: 1.3rem; font-weight: 700; line-height: 1.2;}
.st-key-hdr button, .st-key-hdr button p {font-size: .9rem !important;}
.st-key-hdr button {padding-left: .5rem; padding-right: .5rem;}
.badge {display:inline-block; padding:2px 10px; border-radius:999px; font-size:.74rem; font-weight:600;
        border:1px solid; margin-left:8px; vertical-align:middle; white-space:nowrap;}
.badge-ai {background:rgba(56,139,253,.16); border-color:rgba(56,139,253,.65);}
.badge-rules {background:rgba(210,153,34,.16); border-color:rgba(210,153,34,.75);}
.badge-demo {background:rgba(163,113,247,.16); border-color:rgba(163,113,247,.65);}
.chip {display:inline-block; padding:1px 9px; border-radius:999px; font-size:.72rem; font-weight:600;
       border:1px solid; white-space:nowrap;}
.chip-ready {background:rgba(46,160,67,.18); border-color:rgba(46,160,67,.75);}
.chip-review {background:rgba(210,153,34,.18); border-color:rgba(210,153,34,.8);}
.chip-cant {background:rgba(248,81,73,.18); border-color:rgba(248,81,73,.8);}
.chip-approved {background:rgba(56,139,253,.18); border-color:rgba(56,139,253,.75);}
.chip-rejected {background:rgba(128,128,128,.2); border-color:rgba(128,128,128,.7);}
.chip-live {background:rgba(163,113,247,.18); border-color:rgba(163,113,247,.7); margin-left:4px;}
/* inbox rows: a bordered card with an invisible full-size button on top, so the whole row is clickable */
div[class*="st-key-row_"] {position:relative; border:1px solid rgba(128,128,128,.35); border-radius:8px;
        padding:8px 10px 12px; margin-bottom:4px; gap:0;}
div[class*="st-key-row_"]:hover {border-color:rgba(88,166,255,.8);}
div[class*="st-key-pick_"] {position:absolute; inset:0; z-index:2; width:100%; height:100%;}
div[class*="st-key-pick_"] [data-testid="stButton"] {width:100%; height:100%;}
div[class*="st-key-pick_"] button {width:100%; height:100%; min-height:100%; opacity:0; cursor:pointer;}
div[class*="st-key-row_"] [data-testid="stMarkdownContainer"], div[class*="st-key-row_"] [data-testid="stMarkdownContainer"] > * {margin-bottom:0 !important;}
.row-name {font-weight:600; font-size:.9rem; line-height:1.3;}
.row-subj {opacity:.8; font-size:.8rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.row-meta {display:flex; justify-content:space-between; align-items:center; margin-top:4px; font-size:.76rem;}
.sec-h {font-size:.72rem; letter-spacing:.07em; text-transform:uppercase; opacity:.65; margin:12px 0 5px;
        font-weight:700;}
.found {font-size:.93rem; line-height:1.5;}
.card {border:1px solid rgba(128,128,128,.35); border-left-width:4px; border-radius:8px;
       padding:7px 12px; margin-bottom:7px;}
.card-warn {border-left-color:#d29922;} .card-ok {border-left-color:#2ea043;}
.card-bad {border-left-color:#f85149;} .card-info {border-left-color:#388bfd;}
.card-title {font-weight:600; font-size:.88rem;}
.card-why {font-size:.83rem; opacity:.85; line-height:1.4;}
.change {font-size:.88rem; padding:1px 0; line-height:1.4;}
.change-add::before {content:"+ "; color:#2ea043; font-weight:700;}
.change-del::before {content:"\\2212  "; color:#f85149; font-weight:700;}
.muted {opacity:.65; font-size:.83rem;}
.email-meta {font-size:.9rem; line-height:1.6;}
.email-body {border:1px solid rgba(128,128,128,.35); border-radius:8px; padding:12px 14px;
             line-height:1.55; font-size:.93rem; margin-top:8px;}
.att {display:inline-block; border:1px dashed rgba(128,128,128,.8); border-radius:6px;
      padding:1px 8px; font-size:.78rem; margin:0 6px 4px 0;}
.legend span[style] {padding:1px 7px; border-radius:3px; color:#000; font-size:.76rem; margin-right:6px;}
</style>
"""


# ---------- state helpers ----------

def has_key() -> bool:
    return extractor.active_mode() == "llm"


def current_mode() -> str:
    """'llm' or 'rules'. The header radio (only shown when a key exists) can force rules-only."""
    if has_key() and (st.session_state.get("mode_choice") or "AI") == "AI":
        return "llm"
    return "rules"


def decisions() -> dict:
    """email id -> 'Approved' / 'Rejected', read from the audit log (the single source of truth)."""
    out = {}
    path = config.AUDIT_LOG_CSV
    if path.exists():
        for row in pipeline._read_audit(path):
            if row["action"].startswith("approve"):
                out[row["email_id"]] = "Approved"
            elif row["action"] == "reject":
                out[row["email_id"]] = "Rejected"
    return out


def live_items() -> dict:
    """Emails typed into the app this session (LIVE-1, LIVE-2 ...). They never touch data/emails."""
    return st.session_state.setdefault("live", {})


def all_ids() -> list:
    """Typed-live emails (newest first) above the sample emails."""
    return list(reversed(list(live_items()))) + pipeline.list_email_ids()


def load_any(eid: str) -> dict:
    return live_items()[eid] if eid in live_items() else pipeline.load_email(eid)


def get_result(eid: str):
    cache = st.session_state.setdefault("results", {})
    key = (current_mode(), eid)
    if key not in cache:
        email = load_any(eid)
        cache[key] = (live_email.process_live(email, current_mode()) if eid in live_items()
                      else pipeline.process_email(email, mode=current_mode()))
    return cache[key]


def ensure_all():
    ids = all_ids()
    if any((current_mode(), e) not in st.session_state.get("results", {}) for e in ids):
        with st.spinner("Reading emails..."):
            for e in ids:
                get_result(e)


def rerun_all():
    """Drop every cached result and read all emails again with the current tracker."""
    st.session_state["results"] = {}
    ensure_all()


def invalidate_client(client_id: str):
    """After a tracker change, drop cached results for that client's emails. They are re-read
    (against the updated tracker) the next time they are shown, which is immediately."""
    cache = st.session_state.get("results", {})
    for key in [k for k, r in cache.items() if r["client"] and r["client"]["client_id"] == client_id]:
        del cache[key]
        for stale in (f"draftobj_{key[1]}", f"da_{key[1]}"):  # a draft built on old tracker data is stale too
            st.session_state.pop(stale, None)


def status_of(res: dict, decision) -> str:
    if decision:
        return decision
    if res["client"] is None:
        return "Can't process"
    return "Ready" if res["status"] == "proposed update" else "Needs review"


def sender_name(email: dict) -> str:
    name, addr = parseaddr(email.get("from", ""))
    return name or addr


def select_email(eid: str):
    """Button callback: callbacks run before widgets are built, so this is safe."""
    st.session_state["selected"] = eid


def advance_selection(done_eid: str):
    """Move the selection to the next email that has no decision yet."""
    ids, done = all_ids(), decisions()
    start = ids.index(done_eid) if done_eid in ids else -1
    for e in ids[start + 1:] + ids[:start + 1]:
        if e not in done:
            st.session_state["selected"] = e
            return


def do_approve(eid: str, changes: dict, edited: bool = False):
    """Approve (human click). Then refresh affected results and move to the next undone email."""
    res = get_result(eid)
    out = pipeline.approve(load_any(eid), changes, edited=edited)
    if out["ok"]:
        invalidate_client(res["client"]["client_id"])
    st.session_state["flash"] = out["message"]
    advance_selection(eid)


def do_reject(eid: str, reason: str):
    pipeline.reject(load_any(eid), reason)
    st.session_state["flash"] = "Rejected. The tracker is unchanged and this is recorded in the audit log."
    advance_selection(eid)


def do_reset():
    demo_reset.reset_demo_data()
    for live_id in list(live_items()):  # typed-live emails and their drafts go too
        for stale in (f"draftobj_{live_id}", f"da_{live_id}"):
            st.session_state.pop(stale, None)
    st.session_state["live"] = {}
    rerun_all()
    st.session_state["selected"] = pipeline.list_email_ids()[0]
    st.session_state["flash"] = "Demo data reset: tracker restored, audit log deleted, emails re-read."


# ---------- text helpers (plain code, no LLM) ----------

def highlight(body: str, extraction: dict) -> str:
    """Return the email body as HTML with every quoted piece of evidence highlighted."""
    spans = {}  # (start, end) -> list of (kind, label)
    groups = [("document", extraction["documents_mentioned"], lambda i: i["type"]),
              ("life change", extraction["life_changes"], lambda i: i["type"]),
              ("question", extraction["questions_from_client"], lambda i: "question")]
    for kind, items, label in groups:
        for item in items:
            pattern = r"\s+".join(re.escape(w) for w in item["quote"].split())
            m = re.search(pattern, body, re.I)
            if m:
                spans.setdefault((m.start(), m.end()), []).append((kind, label(item)))
    out, pos = [], 0
    for (start, end), tags in sorted(spans.items()):
        if start < pos:  # partial overlap with an earlier highlight: skip
            continue
        out.append(html.escape(body[pos:start]))
        tip = "; ".join(f"{k}: {l}" for k, l in tags)
        out.append(f'<mark style="background:{KIND_COLORS[tags[0][0]]};color:#000;padding:1px 3px;'
                   f'border-radius:3px" title="{html.escape(tip)}">{html.escape(body[start:end])}</mark>')
        pos = end
    out.append(html.escape(body[pos:]))
    return "".join(out).replace("\n", "<br>")


def what_i_found(res: dict, email: dict) -> str:
    """One plain-English sentence built from the extraction. Plain code, no LLM."""
    if res["client"] is None:
        return "This email is from a sender who is not in the tracker, so I cannot match it to a client."
    if res["error"]:
        return "I could not read this email automatically, so a person needs to read it."
    first = res["client"]["name"].split(" ")[0]
    ext, parts = res["extraction"], []
    if ext["documents_mentioned"]:
        parts.append("mentions " + ", ".join(checks.doc_label(d) for d in ext["documents_mentioned"]))
    if ext["life_changes"]:
        parts.append("reports " + ", ".join(
            f"{c['type']} ({c['amount_text']})" if c.get("amount_text") else c["type"]
            for c in ext["life_changes"]))
    n = len(ext["questions_from_client"])
    if n:
        parts.append(f"asks {n} question{'s' if n > 1 else ''}")
    n_att = len(email["attachments"])
    att = f" It has {n_att} attachment{'s' if n_att != 1 else ''}." if n_att else " It has no attachments."
    if not parts:
        return f"{first}'s email does not clearly mention any documents, life changes or questions." + att
    return f"{first}'s email " + "; it ".join(parts) + "." + att


def change_lines(res: dict, email: dict) -> list:
    """Only what would actually be new, as (kind, text) with friendly field names."""
    client, lines = res["client"], []
    for field in config.LIST_FIELDS:
        have = {i.lower() for i in checks.split_list(client[field])}
        for item in res["changes"][field]:
            if item.lower() not in have:
                lines.append(("add", f"{FIELD_NAMES[field]}: {item}"))
    new_employer = res["changes"].get("current_employer")
    if new_employer and new_employer != client.get("current_employer", ""):
        last = client.get("employer_last_year", "")
        keep = f" (last year's employer, {last}, stays as it is)" if last else ""
        lines.append(("add", f"Current employer: {new_employer}{keep}"))
    for item in checks.find_removals(client, res["changes"], email["attachments"]):
        lines.append(("del", f"No longer needed from client: {item} (matching file received)"))
    return lines


def html_card(kind: str, title: str, why: str = "") -> str:
    why_html = f'<div class="card-why">{html.escape(why)}</div>' if why else ""
    return f'<div class="card card-{kind}"><div class="card-title">{html.escape(title)}</div>{why_html}</div>'


# ---------- dialogs ----------

@st.dialog("Try your own email", width="large")
def live_dialog():
    tracker = pipeline.load_tracker()
    st.caption("Type an email and it runs through the same checks as the sample emails. "
               "It exists only in this session and is not saved with the sample emails.")
    with st.form("live_form"):
        sender = st.selectbox("From", live_email.sender_options(tracker))
        subject = st.text_input("Subject")
        body = st.text_area("Body", height=180)
        attachments = st.text_input("Attachment file names (separate with commas)",
                                    placeholder="W2_Acme.pdf, scan001.pdf")
        submitted = st.form_submit_button("Run on this email", type="primary")
    if submitted:
        live_id = live_email.next_live_id(live_items())
        email = live_email.build_email(live_id, sender, subject, body, attachments, tracker)
        with st.spinner("Reading the email..."):
            result = live_email.process_live(email, current_mode())
        live_items()[live_id] = email
        st.session_state.setdefault("results", {})[(current_mode(), live_id)] = result
        st.session_state["selected"] = live_id
        st.rerun()


@st.dialog("Tracker & audit log", width="large")
def tracker_dialog():
    st.markdown("**Tracker** (`data/tracker.csv`)")
    st.dataframe(pd.DataFrame(pipeline.load_tracker()), hide_index=True, use_container_width=True)
    st.markdown("**Audit log** (`data/audit_log.csv`)")
    if config.AUDIT_LOG_CSV.exists():
        st.dataframe(pd.read_csv(config.AUDIT_LOG_CSV), hide_index=True, use_container_width=True)
    else:
        st.caption("Empty. Nothing has been approved or rejected yet.")


@st.dialog("Edit what will be added", width="large")
def edit_dialog(eid: str):
    res = get_result(eid)
    client = res["client"]
    st.caption("Click the x on anything you do not want added. To add your own entry, click in a box, "
               "type it and press Enter. The three lists are only added to, never replaced.")
    edited = {}
    for f in config.LIST_FIELDS:
        options = list(dict.fromkeys(res["changes"][f]))
        picked = st.multiselect(FIELD_NAMES[f], options, default=options, key=f"ms_{eid}_{f}",
                                accept_new_options=True, placeholder="Type your own entry, then press Enter")
        st.markdown(f'<div class="muted" style="margin:-10px 0 8px">Already in tracker: '
                    f'{html.escape(client[f] or "(empty)")}</div>', unsafe_allow_html=True)
        edited[f] = [i.strip() for i in picked if i.strip()]
    current_now, last = client.get("current_employer", ""), client.get("employer_last_year", "")
    employer = st.text_input("Current employer", value=res["changes"].get("current_employer") or "",
                             key=f"ce_{eid}", placeholder="Leave empty to keep it as it is")
    st.markdown('<div class="muted" style="margin:-10px 0 8px">' + html.escape(
        f"Currently on file: {current_now or '(empty)'}. Last year's employer ({last or 'none'}) is never changed.")
        + "</div>", unsafe_allow_html=True)
    edited["current_employer"] = employer.strip() or None
    reviewed = True
    if res["flags"]:  # same gate as the main panel, repeated here because the popup hides that panel
        reviewed = st.checkbox("I have reviewed the flags", key=f"revd_{eid}",
                               value=st.session_state.get(f"rev_{eid}", False))
    nothing = not any(edited.values())
    if nothing:
        st.info("Nothing selected, so there is nothing to add.")
    if st.button("Approve edited changes", type="primary", disabled=not reviewed or nothing, key=f"appe_{eid}"):
        do_approve(eid, edited, edited=True)
        st.rerun()


COPY_BUTTON = """
<style>
  html, body {margin:0; background:transparent;}
  button {box-sizing:border-box; width:100%; min-height:40px; padding:4px 12px; border-radius:8px; cursor:pointer;
          font-family:"Source Sans Pro", sans-serif; font-size:16px; line-height:1.6; font-weight:400;
          background:transparent; color:#31333f; border:1px solid rgba(49,51,63,.2);}
  button:hover {border-color:#388bfd; color:#388bfd;}
  button:active {background:rgba(56,139,253,.12);}
  @media (prefers-color-scheme: dark) {button {color:#fafafa; border-color:rgba(250,250,250,.2);}}
</style>
<button id="c">Copy</button>
<script>
  const btn = document.getElementById("c");
  const parentDoc = (() => { try { return window.parent.document; } catch (e) { return null; } })();
  // Use the app's own text colour so the button is readable whatever theme is active.
  try {
    const colour = getComputedStyle(parentDoc.querySelector(".stApp")).color;
    btn.style.color = colour;
    btn.style.borderColor = "color-mix(in srgb, " + colour + " 25%, transparent)";
    btn.addEventListener("mouseenter", () => { btn.style.color = "#388bfd"; btn.style.borderColor = "#388bfd"; });
    btn.addEventListener("mouseleave", () => {
      btn.style.color = colour;
      btn.style.borderColor = "color-mix(in srgb, " + colour + " 25%, transparent)";
    });
  } catch (e) {}
  function currentText() {  // read the box as it is NOW, including edits that are not saved yet
    try { return parentDoc.querySelector(".st-key-__KEY__ textarea").value; }
    catch (e) { return ""; }
  }
  function showFailure(show) {  // the one-line message lives in the page, right under the buttons
    try { parentDoc.getElementById("copymsg-__KEY__").style.display = show ? "block" : "none"; } catch (e) {}
  }
  let timer = null;
  btn.addEventListener("click", async () => {
    const text = currentText();
    let ok = false;
    if (text) {
      try { await navigator.clipboard.writeText(text); ok = true; } catch (e) {}
      if (!ok) {  // stricter browsers: select and use the copy command
        const ta = document.createElement("textarea");
        ta.value = text; document.body.appendChild(ta); ta.select();
        try { ok = document.execCommand("copy"); } catch (e) {}
        ta.remove();
      }
    }
    showFailure(!ok);
    btn.textContent = ok ? "Copied" : "Copy";
    clearTimeout(timer);
    timer = setTimeout(() => { btn.textContent = "Copy"; }, 2000);
  });
</script>
"""


@st.dialog("Draft reply to client", width="large")
def reply_dialog(eid: str):
    res, email = get_result(eid), load_any(eid)
    obj_key, box_key = f"draftobj_{eid}", f"da_{eid}"
    if obj_key not in st.session_state:
        with st.spinner("Writing draft..."):
            d = reply.make_draft(res, email, mode=current_mode())
        st.session_state[obj_key] = d
        st.session_state[box_key] = "Subject: " + d["subject"] + "\n\n" + d["body"]
    d = st.session_state[obj_key]
    if d["source"] == "none":
        st.info(d["note"])
        return
    st.text_area("Draft reply", height=330, key=box_key)
    if re.search(r"\[[^\]]+\]", st.session_state[box_key]):  # only when the text really has a placeholder
        st.caption("Anything in [square brackets] is a detail to fill in before sending.")
    c1, c2, _ = st.columns([1.3, 1.3, 3])
    if c1.button("Write it again", key=f"regen_{eid}", use_container_width=True):
        del st.session_state[obj_key], st.session_state[box_key]
        st.rerun(scope="fragment")
    with c2:
        # The HTML must not change between reruns (or the iframe reloads and "Copied" vanishes),
        # so it reads the box's live text from the page instead of embedding it.
        components.html(COPY_BUTTON.replace("__KEY__", box_key), height=44)
    st.markdown(f'<div id="copymsg-{box_key}" class="muted" style="display:none">'
                "Copy did not work in this browser. Select the text in the box and press Ctrl+C.</div>",
                unsafe_allow_html=True)
    st.caption("Nothing is sent from here. Copy the draft into your email client.")


# ---------- header ----------

st.markdown(CSS, unsafe_allow_html=True)
mode = current_mode()

with st.container(key="hdr"):  # the key gives the header buttons their own compact style (see CSS)
    h = st.columns([5.0, 1.1, 1.55, 1.55, 1.65, 1.55], vertical_alignment="center")
mode_badge = ('<span class="badge badge-ai">AI mode (Claude)</span>' if mode == "llm"
              else '<span class="badge badge-rules">Rules-only (no AI)</span>')
h[0].markdown(f'<span class="app-title">Tax-season tracker assistant</span>{mode_badge}'
              f'<span class="badge badge-demo">DEMO DATA</span>', unsafe_allow_html=True)
with h[1]:
    if has_key():
        st.segmented_control("Extractor", ["AI", "Rules"], default="AI",
                             key="mode_choice", label_visibility="collapsed",
                             help="Rules-only needs no API key: simple keyword matching.")
    else:
        st.caption("No API key: rules-only")
if h[2].button("Try your own email", type="primary", use_container_width=True):
    live_dialog()
with h[3].popover("What is simulated", use_container_width=True):
    st.markdown("- **Emails** are files in `data/emails` or typed in here, not a real inbox\n"
                "- **Attachments** are filenames only; no PDF is read\n"
                "- **Rules table** is illustrative, **not tax advice**\n"
                "- **All people and data are fake** (example.com)\n"
                "- **Replies** are drafts; nothing is ever sent\n\n"
                "The tool only *proposes*. It never writes to the tracker without an Approve click.")
if h[4].button("Tracker & audit log", use_container_width=True):
    tracker_dialog()
with h[5].popover("Reset demo data", use_container_width=True):
    st.warning("Restore the original tracker and delete the audit log?")
    st.button("Yes, reset", type="primary", on_click=do_reset, key="confirm_reset")

if "flash" in st.session_state:
    st.toast(st.session_state.pop("flash"))

# ---------- data for all panes ----------

ids = all_ids()
ensure_all()
done = decisions()
if st.session_state.get("selected") not in ids:
    st.session_state["selected"] = ids[0]
selected = st.session_state["selected"]
statuses = {e: status_of(get_result(e), done.get(e)) for e in ids}
st.markdown(f"<style>.st-key-row_{selected} {{border-color:#58a6ff !important; "
            f"background:rgba(56,139,253,.13); border-width:2px;}}</style>", unsafe_allow_html=True)

left, middle, right = st.columns([25, 40, 35], gap="medium")

# ---------- LEFT: inbox ----------

with left:
    n_done = sum(1 for s in statuses.values() if s in ("Approved", "Rejected"))
    st.markdown(f"**Inbox** &nbsp;<span class='muted'>{n_done} of {len(ids)} done</span>", unsafe_allow_html=True)
    st.progress(n_done / len(ids))
    counts = {k: sum(1 for s in statuses.values() if f(s)) for k, f in FILTERS.items()}
    flt = st.pills("Filter", list(FILTERS), default="All", key="filter",
                   format_func=lambda k: f"{SHORT.get(k, k)} {counts[k]}", label_visibility="collapsed") or "All"
    with st.container(height=LIST_HEIGHT, border=False):
        for eid in ids:
            if not FILTERS[flt](statuses[eid]):
                continue
            email, res = load_any(eid), get_result(eid)
            live_chip = ' <span class="chip chip-live">Typed live</span>' if eid in live_items() else ""
            n_flags = len(res["flags"])
            with st.container(key=f"row_{eid}"):
                st.markdown(
                    f'<div class="row-name">{html.escape(sender_name(email))}</div>'
                    f'<div class="row-subj">{html.escape(email["subject"] or "(no subject)")}</div>'
                    f'<div class="row-meta"><span><span class="chip {CHIP_CLASS[statuses[eid]]}">{statuses[eid]}</span>{live_chip}</span>'
                    f'<span class="muted">{n_flags} flag{"s" if n_flags != 1 else ""}</span></div>',
                    unsafe_allow_html=True)
                st.button("Open", key=f"pick_{eid}", on_click=select_email, args=(eid,))

email, res = load_any(selected), get_result(selected)
client, decision = res["client"], done.get(selected)

# ---------- MIDDLE: the email ----------

with middle:
    with st.container(height=PANE_HEIGHT, border=True):
        atts = "".join(f'<span class="att">{html.escape(a)}</span>' for a in email["attachments"]) or \
            '<span class="muted">none</span>'
        st.markdown(
            f'<div class="email-meta"><b>From:</b> {html.escape(email["from"])}<br>'
            f'<b>Subject:</b> {html.escape(email["subject"] or "(none)")}<br>'
            f'<b>Attachments</b> <span class="muted">(filenames only, simulated)</span>: {atts}</div>',
            unsafe_allow_html=True)
        legend = "".join(f'<span style="background:{c}">{k}</span>' for k, c in KIND_COLORS.items())
        st.markdown(f'<div class="legend" style="margin-top:6px">{legend}<span class="muted">= quoted evidence</span></div>',
                    unsafe_allow_html=True)
        body_html = highlight(email["body"], res["extraction"]) if email["body"] else "<i>(empty body)</i>"
        st.markdown(f'<div class="email-body">{body_html}</div>', unsafe_allow_html=True)

# ---------- RIGHT: findings, attention, changes, actions ----------

with right:
    with st.container(height=PANE_HEIGHT, border=True):
        st.markdown('<div class="sec-h" style="margin-top:0">What I found</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="found">{html.escape(what_i_found(res, email))}</div>', unsafe_allow_html=True)

        st.markdown('<div class="sec-h">Needs your attention</div>', unsafe_allow_html=True)
        if client is None:
            st.markdown(html_card("bad", "Can't process: sender is not in the tracker",
                                  "I cannot tell which client this is, so there is nothing to update."),
                        unsafe_allow_html=True)
        elif res["flags"]:
            for f in res["flags"]:
                st.markdown(html_card("warn", FLAG_TITLES.get(f["code"], "Please check"), f["message"]),
                            unsafe_allow_html=True)
        else:
            st.markdown(html_card("ok", "Nothing needs your attention", "All checks passed and nothing conflicts."),
                        unsafe_allow_html=True)

        if client is not None:
            st.markdown('<div class="sec-h">Changes if you approve</div>', unsafe_allow_html=True)
            lines = change_lines(res, email)
            if lines:
                st.markdown("".join(f'<div class="change change-{k}">{html.escape(t)}</div>' for k, t in lines),
                            unsafe_allow_html=True)
                st.markdown('<div class="muted" style="margin-top:4px">' + html.escape(
                    "Lists are added to, never replaced. Last year's employer is never changed.") + "</div>",
                            unsafe_allow_html=True)
            else:
                st.markdown('<div class="muted">Nothing new to add: already recorded.</div>', unsafe_allow_html=True)

        st.markdown('<div class="sec-h">Your decision</div>', unsafe_allow_html=True)
        if decision:
            st.markdown(html_card("info", f"{decision}", "Recorded in the audit log. Nothing more to do here."),
                        unsafe_allow_html=True)
        else:
            reviewed = True
            if client is not None and res["flags"]:
                reviewed = st.checkbox("I have reviewed the flags", key=f"rev_{selected}")
            reject_open = st.session_state.get(f"reject_open_{selected}", False)
            b = st.columns(3 if client is not None else 1)
            if client is not None:
                if b[0].button("Approve", type="primary", disabled=not reviewed, key=f"app_{selected}",
                               use_container_width=True):
                    do_approve(selected, res["changes"])
                    st.rerun()
                if b[1].button("Edit", key=f"edit_{selected}", use_container_width=True):
                    edit_dialog(selected)
            if b[-1].button("Reject", key=f"rej_{selected}", use_container_width=True,
                            on_click=lambda e=selected: st.session_state.update({f"reject_open_{e}": True})):
                pass
            if reject_open:
                reason = st.text_input("Reason for rejecting (optional)", key=f"why_{selected}")
                r1, r2 = st.columns(2)
                if r1.button("Confirm reject", key=f"rejc_{selected}", use_container_width=True):
                    do_reject(selected, reason)
                    st.session_state[f"reject_open_{selected}"] = False
                    st.rerun()
                r2.button("Cancel", key=f"rejx_{selected}", use_container_width=True,
                          on_click=lambda e=selected: st.session_state.update({f"reject_open_{e}": False}))

        can_draft, draft_note = reply.draft_allowed(res)
        if st.button("Draft reply to client", key=f"dr_{selected}", disabled=not can_draft,
                     help=None if can_draft else draft_note):
            reply_dialog(selected)
        if not can_draft:
            st.markdown(f'<div class="muted">{html.escape(draft_note)}</div>', unsafe_allow_html=True)

