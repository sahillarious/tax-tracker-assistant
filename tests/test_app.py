"""UI tests (headless, no browser). The app is pointed at temporary copies of the data
so these tests can never touch the real tracker."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import config
import pipeline

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Rules-only mode + tracker/audit paths redirected to a temp folder."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    tracker, audit = tmp_path / "tracker.csv", tmp_path / "audit_log.csv"
    shutil.copy(config.TRACKER_ORIGINAL_CSV, tracker)
    monkeypatch.setattr(config, "TRACKER_CSV", tracker)
    monkeypatch.setattr(config, "AUDIT_LOG_CSV", audit)
    return tracker, audit


def start():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(APP, default_timeout=30).run()


def button(at, key):
    return next(b for b in at.button if b.key == key)


def page_text(at):
    import html
    return html.unescape(" ".join(m.value for m in at.markdown))


def test_app_loads_in_rules_mode_and_does_not_touch_tracker(sandbox):
    tracker, audit = sandbox
    before = tracker.read_bytes()
    at = start()
    assert not at.exception
    assert "Rules-only (no AI)" in page_text(at) and "DEMO DATA" in page_text(at)
    assert tracker.read_bytes() == before and not audit.exists()


def test_clicking_an_inbox_row_selects_that_email(sandbox):
    at = start()
    for eid in ["E01", "E02", "E08", "E10", "E01"]:  # the old Open-button crash is gone
        button(at, f"pick_{eid}").click().run()
        assert not at.exception, eid
        assert at.session_state["selected"] == eid
        assert pipeline.load_email(eid)["from"] in page_text(at)


def test_evidence_is_highlighted_and_flags_have_no_codes(sandbox):
    at = start()
    button(at, "pick_E01").click().run()
    text = page_text(at)
    assert "<mark" in text                              # quoted evidence highlighted
    assert "Employer changed" in text                   # plain-language card title
    assert "employer_conflict" not in text              # technical code never shown
    assert "Employer differs from last year (Beta Corp → Acme)" in text


def test_unknown_sender_cannot_be_approved(sandbox):
    at = start()
    button(at, "pick_E08").click().run()
    assert not at.exception
    assert "Can't process: sender is not in the tracker" in page_text(at)
    assert not [b for b in at.button if b.label == "Approve"]


def test_review_gate_blocks_approve_until_ticked(sandbox):
    at = start()
    button(at, "pick_E04").click().run()                # flagged email
    assert button(at, "app_E04").disabled
    at.checkbox(key="rev_E04").check().run()
    assert not button(at, "app_E04").disabled


def test_approve_refreshes_results_and_moves_to_next_undone_email(sandbox):
    tracker, audit = sandbox
    at = start()
    button(at, "pick_E02").click().run()
    assert not button(at, "app_E02").disabled           # clean email: no tick needed
    button(at, "app_E02").click().run()
    assert not at.exception and audit.exists()
    assert "1098" in at.session_state["results"][("rules", "E02")]["client"]["docs_received"]
    assert at.session_state["selected"] == "E03"        # next email without a decision
    assert "1 of 11 done" in page_text(at)


def test_reject_reason_appears_only_after_clicking_reject(sandbox):
    tracker, audit = sandbox
    before = tracker.read_bytes()
    at = start()
    button(at, "pick_E02").click().run()
    assert not [t for t in at.text_input if t.key == "why_E02"]
    button(at, "rej_E02").click().run()
    assert [t for t in at.text_input if t.key == "why_E02"]
    at.text_input(key="why_E02").set_value("wrong client")
    button(at, "rejc_E02").click().run()
    assert tracker.read_bytes() == before               # reject never changes the tracker
    assert "reject" in audit.read_text(encoding="utf-8")
    assert "1 of 11 done" in page_text(at)


def test_reset_needs_confirm_click_and_restores_original_bytes(sandbox):
    tracker, audit = sandbox
    original = config.TRACKER_ORIGINAL_CSV.read_bytes()
    at = start()
    button(at, "pick_E02").click().run()
    button(at, "app_E02").click().run()
    assert tracker.read_bytes() != original and audit.exists()
    assert "Restore the original tracker" in " ".join(w.value for w in at.warning)  # the confirm prompt
    assert tracker.read_bytes() != original             # nothing resets until "Yes, reset" is clicked
    button(at, "confirm_reset").click().run()
    assert tracker.read_bytes() == original and not audit.exists()
    assert "0 of 11 done" in page_text(at) and not at.exception


def test_reset_function_restores_exact_bytes(tmp_path):
    import demo_reset
    tracker, audit = tmp_path / "t.csv", tmp_path / "a.csv"
    shutil.copy(config.TRACKER_ORIGINAL_CSV, tracker)
    email = pipeline.load_email("E01")
    changes = pipeline.process_email(email, mode="rules")["changes"]
    assert pipeline.approve(email, changes, tracker_path=tracker, audit_path=audit)["ok"]
    assert tracker.read_bytes() != config.TRACKER_ORIGINAL_CSV.read_bytes()
    demo_reset.reset_demo_data(tracker=tracker, audit=audit)
    assert tracker.read_bytes() == config.TRACKER_ORIGINAL_CSV.read_bytes() and not audit.exists()


def test_original_tracker_is_pristine():
    rows = pipeline.load_tracker(config.TRACKER_ORIGINAL_CSV)
    assert len(rows) == 8
    assert next(r for r in rows if r["client_id"] == "C01")["docs_received"] == "W-2 (Beta Corp)"
    assert all(r["docs_missing"] == "" and r["life_changes"] == "" for r in rows)


def test_draft_button_is_disabled_with_a_note_for_unknown_sender_and_injection(sandbox):
    at = start()
    for eid in ["E08", "E10"]:
        button(at, f"pick_{eid}").click().run()
        assert button(at, f"dr_{eid}").disabled, eid
        assert "needs a person to write the reply" in page_text(at)
    button(at, "pick_E03").click().run()
    assert not button(at, "dr_E03").disabled


def test_draft_dialog_opens_with_subject_and_specific_text(sandbox):
    at = start()
    button(at, "pick_E03").click().run()
    button(at, "dr_E03").click().run()
    assert not at.exception
    box = at.text_area(key="da_E03").value
    assert box.startswith("Subject: Re: tax docs - what we have and what's next")
    assert "W-2 (Contoso Labs)" in box and "1099-B is coming next week" in box
    assert "needs a person" not in box


def test_edit_dialog_has_its_own_review_gate_and_free_entry_boxes(sandbox):
    """AppTest reruns the whole script on every click, which closes a popup, so the full click-through
    (type an entry, tick, approve) was checked in a real browser. Here: what the popup shows when opened."""
    at = start()
    button(at, "pick_E01").click().run()                # flagged email
    button(at, "edit_E01").click().run()
    assert not at.exception
    assert button(at, "appe_E01").disabled              # gate is inside the popup now
    assert at.checkbox(key="revd_E01").value is False
    for field in config.LIST_FIELDS:                    # one box per field, with room to type
        assert at.multiselect(key=f"ms_E01_{field}") is not None
    at2 = start()
    button(at2, "pick_E02").click().run()               # clean email: no flags, so no gate
    button(at2, "edit_E02").click().run()
    assert not button(at2, "appe_E02").disabled


def test_edited_approval_appends_a_manual_entry_and_never_overwrites(tmp_path):
    import shutil
    tracker, audit = tmp_path / "t.csv", tmp_path / "a.csv"
    shutil.copy(config.TRACKER_ORIGINAL_CSV, tracker)
    email = pipeline.load_email("E01")
    changes = {"docs_received": ["W-2 (Acme)", "W-2 (Manual Test Co)"], "docs_missing": [],
               "life_changes": ["Manual override: seasonal work"]}
    assert pipeline.approve(email, changes, edited=True, tracker_path=tracker, audit_path=audit)["ok"]
    row = next(r for r in pipeline.load_tracker(tracker) if r["client_id"] == "C01")
    assert row["docs_received"] == "W-2 (Beta Corp); W-2 (Acme); W-2 (Manual Test Co)"
    assert row["life_changes"] == "Manual override: seasonal work"
    assert row["employer_last_year"] == "Beta Corp"
    assert "approve_edited" in audit.read_text(encoding="utf-8")


# ---------- the reply dialog ----------

def open_reply_dialog(sandbox_at, eid, draft=None):
    """Open the reply dialog for an email. `draft` pre-sets the stored draft (a dict like make_draft returns)."""
    if draft is not None:
        sandbox_at.session_state[f"draftobj_{eid}"] = draft
        sandbox_at.session_state[f"da_{eid}"] = "Subject: " + draft["subject"] + "\n\n" + draft["body"]
    button(sandbox_at, f"pick_{eid}").click().run()
    button(sandbox_at, f"dr_{eid}").click().run()
    assert not sandbox_at.exception
    return sandbox_at


def fake_draft(source="template", body="Hi Priya,\n\nThanks.", failed=None):
    return {"subject": "Re: tax docs - what we have and what's next", "body": body, "source": source, "note": "",
            "failed_draft": failed, "problems": [], "facts": {}, "client_questions": []}


def captions(at):
    return [c.value for c in at.caption]


def test_reply_dialog_has_no_fallback_block_and_a_plain_label(sandbox):
    at = open_reply_dialog(start(), "E03")
    assert at.text_area(key="da_E03").label == "Draft reply"
    assert not [e for e in at.expander if "copy" in e.label.lower()]      # the old expander is gone
    assert not at.code                                                    # and so is its code block
    assert not [b for b in at.button if "copy" in b.label.lower()]        # Copy is the iframe button
    assert "Nothing is sent from here. Copy the draft into your email client." in captions(at)
    assert "Built only from verified facts" not in " ".join(captions(at))


def test_bracket_caption_only_when_the_draft_has_square_brackets(sandbox):
    caption = "Anything in [square brackets] is a detail to fill in before sending."
    with_brackets = open_reply_dialog(start(), "E03", fake_draft(body="Hi [first name],\n\nThanks."))
    assert caption in captions(with_brackets)
    without = open_reply_dialog(start(), "E03", fake_draft(body="Hi Priya,\n\nThanks."))
    assert caption not in captions(without)


def test_dialog_has_no_status_line_and_no_question_box(sandbox):
    """The dialog is just: the draft box, the bracket caption (only with brackets), the buttons and one footer."""
    for draft in (fake_draft(source="ai"), fake_draft(source="template"),
                  fake_draft(source="template", failed="bad draft")):
        at = open_reply_dialog(start(), "E04", draft)
        text = " ".join(captions(at) + [m.value for m in at.markdown])
        assert "Written by AI" not in text and "Built from a template" not in text and "did not pass" not in text
        assert "The client asked this question" not in text and "was not given it" not in text
    real = open_reply_dialog(start(), "E04")        # E04 has a client question: still no box for it
    assert "The client asked" not in " ".join(m.value for m in real.markdown)


# ---------- current employer on screen ----------

def test_changes_list_shows_the_new_current_employer(sandbox):
    at = start()
    button(at, "pick_E01").click().run()
    text = page_text(at)
    assert "Current employer: Acme (last year's employer, Beta Corp, stays as it is)" in text


def test_edit_popup_has_a_current_employer_box_prefilled_with_the_proposal(sandbox):
    at = start()
    button(at, "pick_E01").click().run()
    button(at, "edit_E01").click().run()
    assert not at.exception
    box = at.text_input(key="ce_E01")
    assert box.label == "Current employer" and box.value == "Acme"


def test_approving_in_the_app_sets_current_employer(sandbox):
    tracker, audit = sandbox
    at = start()
    button(at, "pick_E01").click().run()
    at.checkbox(key="rev_E01").check().run()
    button(at, "app_E01").click().run()
    maria = next(r for r in pipeline.load_tracker(tracker) if r["client_id"] == "C01")
    assert (maria["current_employer"], maria["employer_last_year"]) == ("Acme", "Beta Corp")
