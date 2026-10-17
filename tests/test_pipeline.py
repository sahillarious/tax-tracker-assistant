"""Tests for pipeline.py (no real API calls, tracker/audit written to a temp folder)."""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import config
import pipeline


class ExplodingClient:
    """Fails the test if the AI is called at all."""
    def __init__(self):
        self.messages = self

    def create(self, **kw):
        raise AssertionError("LLM must not be called")


@pytest.fixture
def tmp_data(pristine_data):
    """(tracker path, audit path): a pristine temp copy made by conftest.py."""
    return pristine_data


def run(eid, mode="rules"):
    return pipeline.process_email(pipeline.load_email(eid), mode=mode)


def test_unknown_sender_stops_without_calling_llm(tmp_path):
    res = pipeline.process_email(pipeline.load_email("E08"), mode="llm",
                                 client=ExplodingClient(), raw_dir=tmp_path)
    assert res["status"] == "needs review"
    assert [f["code"] for f in res["flags"]] == ["unknown_sender"]


def test_maria_example_proposal():
    res = run("E01")
    assert res["status"] == "needs review"
    assert res["changes"]["docs_received"] == ["W-2 (Acme)"]
    assert res["changes"]["docs_missing"] == ["1099-NEC (if issued)", "Income and expense summary"]
    assert res["changes"]["life_changes"] == ["freelance income (about $5k)"]
    assert "Beta Corp → Acme" in res["reasons"][0]


def test_clean_email_is_proposed_update():
    assert run("E02")["status"] == "proposed update" and run("E02")["flags"] == []


def test_injection_email_makes_no_document():
    res = run("E10")
    assert res["extraction"]["documents_mentioned"] == []
    assert res["changes"]["docs_received"] == []


def test_llm_failure_goes_to_review(tmp_path):
    class Bad:
        def __init__(self):
            self.messages = self
        def create(self, **kw):
            return type("R", (), {"content": [type("C", (), {"text": "not json"})()]})()
    res = pipeline.process_email(pipeline.load_email("E01"), mode="llm", client=Bad(), raw_dir=tmp_path)
    assert res["status"] == "needs review"
    assert res["flags"][0]["code"] == "extraction_failed"


def test_process_email_never_writes_to_tracker():
    before = config.TRACKER_CSV.read_bytes()
    for eid in pipeline.list_email_ids():
        run(eid)
    assert config.TRACKER_CSV.read_bytes() == before


def test_approve_appends_and_is_idempotent(tmp_data):
    tracker, audit = tmp_data
    email = pipeline.load_email("E01")
    changes = run("E01")["changes"]
    r1 = pipeline.approve(email, changes, tracker_path=tracker, audit_path=audit)
    r2 = pipeline.approve(email, changes, tracker_path=tracker, audit_path=audit)
    assert r1["ok"] and not r2["ok"]
    maria = next(r for r in pipeline.load_tracker(tracker) if r["client_id"] == "C01")
    assert maria["docs_received"] == "W-2 (Beta Corp); W-2 (Acme)"  # appended, Beta kept
    assert maria["employer_last_year"] == "Beta Corp"               # never overwritten
    assert maria["docs_missing"].count("1099-NEC") == 1
    with open(audit, newline="", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 1  # second approve wrote nothing


def test_approve_removes_missing_item_and_logs_it(tmp_data):
    tracker, audit = tmp_data
    # Tom is waiting on a 1099-B; then a 1099-B arrives with a matching file.
    rows = pipeline.load_tracker(tracker)
    for r in rows:
        if r["client_id"] == "C04":
            r["docs_missing"] = "1099-B; 1098-E"
    with open(tracker, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    email = {"id": "X1", "from": "tom.becker@example.com", "body": "Attached is my 1099-B.",
             "attachments": ["1099-B_Fidelity.pdf"]}
    res = pipeline.process_email(email, mode="rules")
    out = pipeline.approve(email, res["changes"], tracker_path=tracker, audit_path=audit)
    tom = next(r for r in pipeline.load_tracker(tracker) if r["client_id"] == "C04")
    assert out["removed"] == ["1099-B"]
    assert tom["docs_missing"] == "1098-E"
    assert "1099-B" in tom["docs_received"]
    with open(audit, newline="", encoding="utf-8") as f:
        actions = [r["action"] for r in csv.DictReader(f)]
    assert actions == ["approve", "remove_from_docs_missing"]


def test_reject_changes_nothing(tmp_data):
    tracker, audit = tmp_data
    before = tracker.read_bytes()
    pipeline.reject(pipeline.load_email("E01"), "wrong client", audit_path=audit)
    assert tracker.read_bytes() == before and audit.exists()


def test_old_w2_does_not_satisfy_new_employer_w2():
    res = run("E11")  # tracker has W-2 (Beta Corp); new W-2 from Acme Robotics is only promised
    assert res["changes"]["docs_missing"] == ["W-2 (Acme Robotics)"]
    assert res["changes"]["docs_received"] == []


def test_w2_on_file_from_same_employer_is_not_missing():
    email = {"id": "X2", "from": "maria@example.com", "attachments": [],
             "body": "I started at Beta Corp again. The W-2 from Beta Corp is coming soon."}
    assert pipeline.process_email(email, mode="rules")["changes"]["docs_missing"] == []


# ---------- Maria says her new employer is Acme ----------

def test_e01_proposes_acme_as_the_current_employer_and_e02_proposes_none():
    assert run("E01")["changes"]["current_employer"] == "Acme"
    assert run("E02")["changes"]["current_employer"] is None             # no employer on a mortgage form


def test_approving_maria_sets_current_employer_and_keeps_last_year(tmp_data):
    tracker, audit = tmp_data
    email = pipeline.load_email("E01")
    out = pipeline.approve(email, run("E01")["changes"], tracker_path=tracker, audit_path=audit)
    assert out["ok"] and out["diff"]["current_employer"] == {"before": "", "after": "Acme"}
    maria = next(r for r in pipeline.load_tracker(tracker) if r["client_id"] == "C01")
    assert maria["current_employer"] == "Acme"
    assert maria["employer_last_year"] == "Beta Corp"                    # last year's employer is history
    logged = [json.loads(r["diff"]) for r in pipeline._read_audit(audit) if r["action"] == "approve"]
    assert logged[0]["changes"]["current_employer"] == {"before": "", "after": "Acme"}   # old and new value logged


def test_approving_twice_does_not_change_anything_more(tmp_data):
    tracker, audit = tmp_data
    email, changes = pipeline.load_email("E01"), run("E01")["changes"]
    pipeline.approve(email, changes, tracker_path=tracker, audit_path=audit)
    once = tracker.read_bytes()
    assert not pipeline.approve(email, changes, tracker_path=tracker, audit_path=audit)["ok"]
    assert tracker.read_bytes() == once


def test_after_approval_the_same_employer_is_no_longer_flagged(tmp_data):
    tracker, audit = tmp_data
    pipeline.approve(pipeline.load_email("E01"), run("E01")["changes"], tracker_path=tracker, audit_path=audit)
    again = pipeline.process_email(pipeline.load_email("E01"), mode="rules", tracker=pipeline.load_tracker(tracker))
    assert "employer_conflict" not in [f["code"] for f in again["flags"]]
    assert again["changes"]["current_employer"] is None


def test_edit_can_blank_out_or_change_the_employer(tmp_data):
    tracker, audit = tmp_data
    email = pipeline.load_email("E06")                                   # Raj: Initech differs from Globex
    changes = dict(run("E06")["changes"], current_employer=None)         # staff cleared the box in Edit
    pipeline.approve(email, changes, edited=True, tracker_path=tracker, audit_path=audit)
    raj = next(r for r in pipeline.load_tracker(tracker) if r["client_id"] == "C06")
    assert raj["current_employer"] == ""


def test_an_old_tracker_file_without_the_column_still_loads(tmp_path):
    old = tmp_path / "old.csv"
    old.write_text("client_id,name,email,tax_year,employer_last_year,docs_received,docs_missing,life_changes,"
                   "next_action\nC01,Maria Lopez,maria@example.com,2025,Beta Corp,,,,x\n", encoding="utf-8")
    assert pipeline.load_tracker(old)[0]["current_employer"] == ""
