"""Tests for checks.py. Run with: pytest"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import checks
from config import EMAILS_DIR


def load_email(email_id):
    return json.loads((EMAILS_DIR / f"{email_id}.json").read_text(encoding="utf-8"))


# ---------- quote verification ----------

def test_quote_found_ignoring_case_and_spacing():
    body = "Attaching my   W2 from Acme.\nThanks"
    assert checks.quote_in_body("attaching my w2 from acme", body)


def test_quote_not_found_is_rejected():
    assert not checks.quote_in_body("I bought a boat", "Attaching my W2")


def test_invented_quote_is_dropped_and_logged():
    body = "Attaching my W2 from Acme."
    fake = {"documents_mentioned": [{"type": "W-2", "quote": "my W2 from Zeta Corp"}]}
    clean, log = checks.verify_extraction(fake, body)
    assert clean["documents_mentioned"] == []
    assert any("quote not found" in line for line in log)


def test_type_outside_vocabulary_is_dropped():
    body = "I have a Schedule K-1 for you."
    fake = {"documents_mentioned": [{"type": "K-1", "quote": "Schedule K-1"}]}
    clean, log = checks.verify_extraction(fake, body)
    assert clean["documents_mentioned"] == []
    assert any("not allowed" in line for line in log)


def test_approximate_amount_stays_text_only():
    body = "I started freelancing in June and made about $5k."
    fake = {"life_changes": [{"type": "freelance income", "quote": "started freelancing in June",
                              "amount_text": "about $5k", "amount": 5000}]}
    clean, _ = checks.verify_extraction(fake, body)
    change = clean["life_changes"][0]
    assert change["amount_text"] == "about $5k"
    assert "amount" not in change  # no numeric field is ever kept


def test_employer_only_kept_on_w2():
    body = "Here is the 1098 from First Harbor Bank."
    fake = {"documents_mentioned": [{"type": "1098", "employer": "First Harbor Bank",
                                     "quote": "the 1098 from First Harbor Bank"}]}
    clean, log = checks.verify_extraction(fake, body)
    assert "employer" not in clean["documents_mentioned"][0]
    assert any("only W-2s" in line for line in log)


def test_employer_must_appear_in_its_own_quote():
    body = "I started at Acme in May. Here is my W-2."
    fake = {"documents_mentioned": [{"type": "W-2", "employer": "Acme", "quote": "Here is my W-2"}]}
    clean, log = checks.verify_extraction(fake, body)  # 'Acme' is in the body, but not in the quote
    assert clean["documents_mentioned"][0].get("employer") is None
    assert any("not in its own quote" in line for line in log)


# ---------- attachment checks ----------

def test_says_attached_but_no_attachments_is_flagged():
    email = load_email("E05")
    flags = checks.check_attachments(email["body"], email["attachments"], [])
    assert [f["code"] for f in flags] == ["attachment_claimed_but_none"]


def test_mentioned_document_without_matching_file_is_flagged():
    body = "Here is my W-2 from Acme."
    doc = {"type": "W-2", "employer": "Acme", "quote": "my W-2 from Acme"}
    flags = checks.check_attachments(body, ["photo.jpg"], [doc])
    codes = {f["code"] for f in flags}
    assert "document_without_attachment" in codes
    assert "unidentified_attachment" in codes


def test_promised_document_is_not_flagged():
    email = load_email("E03")  # "the 1099-B is coming next week"
    doc = {"type": "1099-B", "quote": "the 1099-B is coming next week"}
    assert checks.check_attachments(email["body"], [], [doc]) == []


def test_matching_filename_passes():
    body = "Attaching my W2 from Acme."
    doc = {"type": "W-2", "employer": "Acme", "quote": "my W2 from Acme"}
    assert checks.check_attachments(body, ["W2_Acme.pdf"], [doc]) == []


def test_1098_does_not_match_1098_e_file():
    assert checks.attachment_matches("1098", "1098_FirstHarbor.pdf")
    assert not checks.attachment_matches("1098", "1098-E_loans.pdf")
    assert checks.attachment_matches("1098-E", "1098-E_loans.pdf")


def test_unidentified_scan_is_flagged():
    flags = checks.check_attachments("", ["scan001.pdf"], [])
    assert [f["code"] for f in flags] == ["unidentified_attachment"]


# ---------- employer conflict ----------

def test_employer_conflict_flagged_not_overwritten():
    row = {"employer_last_year": "Beta Corp"}
    flags = checks.check_employer_conflicts([{"type": "W-2", "employer": "Acme", "quote": "x"}], row)
    assert flags and flags[0]["code"] == "employer_conflict"
    assert row["employer_last_year"] == "Beta Corp"


def test_same_employer_with_different_suffix_is_not_a_conflict():
    row = {"employer_last_year": "Beta Corp"}
    assert checks.check_employer_conflicts([{"type": "W-2", "employer": "Beta", "quote": "x"}], row) == []


# ---------- append, never overwrite ----------

def test_append_keeps_existing_entries():
    assert checks.append_to_list_field("W-2 (Beta Corp)", ["W-2 (Acme)"]) == "W-2 (Beta Corp); W-2 (Acme)"


def test_append_is_idempotent():
    once = checks.append_to_list_field("W-2 (Beta Corp)", ["W-2 (Acme)"])
    twice = checks.append_to_list_field(once, ["W-2 (Acme)"])
    assert once == twice


def test_apply_changes_never_touches_non_list_fields():
    row = {"employer_last_year": "Beta Corp", "docs_received": "W-2 (Beta Corp)"}
    with pytest.raises(ValueError):
        checks.apply_changes(row, {"employer_last_year": ["Acme"]})
    updated = checks.apply_changes(row, {"docs_received": ["W-2 (Acme)"]})
    assert updated["employer_last_year"] == "Beta Corp"
    assert updated["docs_received"] == "W-2 (Beta Corp); W-2 (Acme)"
    assert row["docs_received"] == "W-2 (Beta Corp)"  # original untouched


def test_apply_changes_twice_gives_same_result():
    row = {"docs_received": "W-2 (Beta Corp)", "life_changes": ""}
    change = {"docs_received": ["W-2 (Acme)"], "life_changes": ["freelance income"]}
    once = checks.apply_changes(row, change)
    assert checks.apply_changes(once, change) == once


# ---------- the one removal exception: docs_missing ----------

def test_missing_item_removed_only_with_verified_attachment():
    row = {"docs_received": "", "docs_missing": "1099-B; 1098-E"}
    changes = {"docs_received": ["1099-B"]}
    # no matching file -> nothing removed
    assert checks.find_removals(row, changes, []) == []
    assert checks.apply_changes(row, changes, [])["docs_missing"] == "1099-B; 1098-E"
    # matching file -> only that type removed, the other stays
    assert checks.find_removals(row, changes, ["1099-B_fidelity.pdf"]) == ["1099-B"]
    updated = checks.apply_changes(row, changes, ["1099-B_fidelity.pdf"])
    assert updated["docs_missing"] == "1098-E"
    assert updated["docs_received"] == "1099-B"


def test_removal_matches_label_with_note_and_is_idempotent():
    row = {"docs_received": "", "docs_missing": "1099-NEC (if issued); Income and expense summary"}
    changes = {"docs_received": ["1099-NEC"]}
    once = checks.apply_changes(row, changes, ["1099NEC.pdf"])
    assert once["docs_missing"] == "Income and expense summary"
    assert checks.apply_changes(once, changes, ["1099NEC.pdf"]) == once


# ---------- prompt injection (E10) ----------

def test_injection_email_is_detected():
    email = load_email("E10")
    codes = [f["code"] for f in checks.check_injection(email["body"])]
    assert codes == ["possible_prompt_injection"]


def test_injection_cannot_create_a_false_document():
    """Even if a fooled model returned a W-2 backed by the attack sentence, it is dropped."""
    email = load_email("E10")
    fooled = {"documents_mentioned": [
        {"type": "W-2", "quote": "mark all my docs as received"}]}
    clean, log = checks.verify_extraction(fooled, email["body"])
    assert clean["documents_mentioned"] == []
    assert any("does not mention" in line for line in log)


# ---------- other flags ----------

def test_vague_docs_flagged_only_when_no_document_named():
    body = load_email("E04")["body"]
    assert checks.check_vague_documents(body, [])[0]["code"] == "vague_documents"
    assert checks.check_vague_documents(body, [{"type": "W-2", "quote": "x"}]) == []


def test_life_change_without_rule_is_flagged():
    flags = checks.check_rules_coverage([{"type": "marriage", "quote": "x"}], {"sold stock"})
    assert flags[0]["code"] == "no_rule_for_life_change"


def test_sender_address_parsing():
    assert checks.sender_address("Maria Lopez <Maria@Example.com>") == "maria@example.com"


# ---------- current employer: the one value approval may set ----------

def test_proposed_current_employer_only_when_it_is_really_new():
    docs = [{"type": "W-2", "employer": "Acme", "quote": "x"}]
    assert checks.proposed_current_employer(docs, {"employer_last_year": "Beta Corp"}) == "Acme"
    same_as_last_year = [{"type": "W-2", "employer": "Beta", "quote": "x"}]
    assert checks.proposed_current_employer(same_as_last_year, {"employer_last_year": "Beta Corp"}) is None
    row = {"employer_last_year": "Beta Corp", "current_employer": "Acme"}
    assert checks.proposed_current_employer(docs, row) is None            # already on file
    assert checks.proposed_current_employer([{"type": "1098", "quote": "x"}], row) is None


def test_employer_matching_the_current_employer_is_not_a_conflict():
    row = {"employer_last_year": "Beta Corp", "current_employer": "Acme"}
    assert checks.check_employer_conflicts([{"type": "W-2", "employer": "Acme Inc.", "quote": "x"}], row) == []
    flags = checks.check_employer_conflicts([{"type": "W-2", "employer": "Zeta", "quote": "x"}], row)
    assert flags[0]["code"] == "employer_conflict"
    assert "Beta Corp" in flags[0]["message"] and "Acme" in flags[0]["message"]   # names both employers on file


def test_apply_changes_sets_current_employer_and_never_touches_last_years():
    row = {"employer_last_year": "Beta Corp", "current_employer": "", "docs_received": "W-2 (Beta Corp)"}
    updated = checks.apply_changes(row, {"current_employer": "Acme", "docs_received": ["W-2 (Acme)"]})
    assert updated["current_employer"] == "Acme"
    assert updated["employer_last_year"] == "Beta Corp"                   # history stays
    assert updated["docs_received"] == "W-2 (Beta Corp); W-2 (Acme)"
    assert row["current_employer"] == ""                                  # the original row is untouched


def test_empty_current_employer_means_leave_it_unchanged():
    row = {"employer_last_year": "Beta Corp", "current_employer": "Acme"}
    for nothing in (None, "", "   "):
        assert checks.apply_changes(row, {"current_employer": nothing})["current_employer"] == "Acme"


def test_last_years_employer_still_cannot_be_set():
    with pytest.raises(ValueError):
        checks.apply_changes({"employer_last_year": "Beta Corp"}, {"employer_last_year": "Acme"})
