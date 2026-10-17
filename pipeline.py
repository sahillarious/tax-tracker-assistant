"""The whole flow for one email: identify client -> extract -> verify -> rules -> compare -> decide.

process_email() only PROPOSES. Nothing is written to the tracker here.
The only function that writes is approve(), and a human triggers it.
"""
import csv
import json
from datetime import datetime

import checks
import config
import extractor


# ---------- loading data ----------

def load_tracker(path=None) -> list:
    with open(path or config.TRACKER_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_rules(path=None) -> list:
    with open(path or config.RULES_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_email(email_id: str) -> dict:
    return json.loads((config.EMAILS_DIR / f"{email_id}.json").read_text(encoding="utf-8"))


def list_email_ids() -> list:
    return sorted(p.stem for p in config.EMAILS_DIR.glob("*.json"))


def find_client(from_field: str, tracker: list):
    """Step 1: match the sender's address to tracker.email."""
    addr = checks.sender_address(from_field)
    return next((r for r in tracker if r["email"].strip().lower() == addr), None)


# ---------- the pipeline ----------

def _result(email, mode, status, flags, row=None, extraction=None, dropped=None, changes=None, error=None, temp=None):
    return {"email_id": email["id"], "status": status, "flags": flags,
            "reasons": [f["message"] for f in flags], "client": row,
            "extraction": extraction or {k: [] for k in extractor.EMPTY},
            "dropped": dropped or [], "mode": mode, "error": error, "temperature_applied": temp,
            "changes": changes or {"docs_received": [], "docs_missing": [], "life_changes": []}}


def process_email(email: dict, mode: str = None, tracker: list = None, rules: list = None,
                  client=None, raw_dir=None) -> dict:
    mode = mode or extractor.active_mode()
    tracker = tracker if tracker is not None else load_tracker()
    rules = rules if rules is not None else load_rules()
    body, attachments = email.get("body", ""), email.get("attachments", [])

    # 1. Who sent it? Unknown sender -> stop. The AI is never called.
    row = find_client(email.get("from", ""), tracker)
    if row is None:
        return _result(email, mode, "needs review",
                       [checks.flag("unknown_sender", "Sender is not in the tracker (unknown sender).")])

    # 2. Extract (AI or rules-only). If the AI fails twice, a human looks at the email.
    ext = extractor.extract(email, mode=mode, client=client, raw_dir=raw_dir)
    flags = checks.check_injection(body)
    if not ext["ok"]:
        flags.append(checks.flag("extraction_failed", "AI extraction failed: " + ext["error"]))
        return _result(email, mode, "needs review", flags, row, error=ext["error"],
                       temp=ext.get("temperature_applied"))

    # 3. Verify: drop anything not backed by a verbatim quote or outside the allowed lists.
    clean, dropped = checks.verify_extraction(ext["extraction"], body)
    docs, lifes = clean["documents_mentioned"], clean["life_changes"]
    flags += checks.check_attachments(body, attachments, docs)
    flags += checks.check_vague_documents(body, docs)
    flags += checks.check_questions(clean["questions_from_client"])

    # 4. Rules lookup: life change -> expected documents.
    rules_types = {r["life_change"] for r in rules}
    flags += checks.check_rules_coverage(lifes, rules_types)
    expected = []  # labels like "1099-NEC (if issued)"
    for lc in lifes:
        for r in rules:
            if r["life_change"] == lc["type"]:
                label = f"{r['expected_doc']} ({r['note']})" if r["note"] else r["expected_doc"]
                if r["note"] == "from new employer":
                    label = r["expected_doc"]  # the note is explanation, not part of the name
                if label not in expected:
                    expected.append(label)

    attached = [d for d in docs if any(checks.attachment_matches(d["type"], f) for f in attachments)]
    have = {checks.doc_type_of(x) for x in checks.split_list(row["docs_received"])} - {"W-2"}
    have |= {d["type"] for d in attached}
    # A W-2 counts only if it is from the right employer: last year's W-2 must not
    # satisfy "W-2 from new employer". Match on type AND employer.
    w2_on_file = {checks.employer_key(x.split(" (", 1)[1].rstrip(")"))
                  for x in checks.split_list(row["docs_received"]) if x.startswith("W-2 (")}
    mentioned_employers = [d["employer"] for d in docs if d["type"] == "W-2" and d.get("employer")]
    missing = []
    for e in expected:
        if checks.doc_type_of(e) == "W-2":
            if any(d["type"] == "W-2" for d in attached) or                any(checks.employer_key(m) in w2_on_file for m in mentioned_employers):
                continue
            e = f"W-2 ({mentioned_employers[0]})" if mentioned_employers else "W-2"
        elif checks.doc_type_of(e) in have:
            continue
        missing.append(e)

    # 5. Compare with the tracker (flag only, never overwrite).
    flags += checks.check_employer_conflicts(docs, row)

    # 6. Decide. Always attach the proposed change set, even when review is needed.
    life_labels = [f"{lc['type']} ({lc['amount_text']})" if lc.get("amount_text") else lc["type"]
                   for lc in lifes]
    changes = {"docs_received": [checks.doc_label(d) for d in attached],
               "docs_missing": missing, "life_changes": life_labels}
    status = "needs review" if flags else "proposed update"
    return _result(email, mode, status, flags, row, clean, dropped, changes,
                   temp=ext.get("temperature_applied"))


# ---------- step 8: approve (human only) ----------

def _read_audit(path) -> list:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_audit(path, email_id, action, diff):
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "email_id", "action", "diff"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), email_id, action,
                    json.dumps(diff, ensure_ascii=False)])


def already_approved(email_id: str, audit_path=None) -> bool:
    return any(r["email_id"] == email_id and r["action"].startswith("approve")
               for r in _read_audit(audit_path or config.AUDIT_LOG_CSV))


def approve(email: dict, changes: dict, edited: bool = False,
            tracker_path=None, audit_path=None) -> dict:
    """Apply human-approved changes. Append-only (see checks.apply_changes), idempotent per email."""
    tracker_path = tracker_path or config.TRACKER_CSV
    audit_path = audit_path or config.AUDIT_LOG_CSV
    if already_approved(email["id"], audit_path):
        return {"ok": False, "message": f"{email['id']} was already approved. Nothing changed."}
    tracker = load_tracker(tracker_path)
    row = find_client(email.get("from", ""), tracker)
    if row is None:
        return {"ok": False, "message": "Unknown sender: cannot update the tracker."}

    attachments = email.get("attachments", [])
    removals = checks.find_removals(row, changes, attachments)
    new_row = checks.apply_changes(row, changes, attachments)
    diff = {f: {"before": row[f], "after": new_row[f]} for f in config.LIST_FIELDS if row[f] != new_row[f]}

    tracker = [new_row if r["client_id"] == row["client_id"] else r for r in tracker]
    with open(tracker_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        w.writeheader()
        w.writerows(tracker)

    _write_audit(audit_path, email["id"], "approve_edited" if edited else "approve",
                 {"client_id": row["client_id"], "changes": diff})
    for item in removals:  # every removal from docs_missing gets its own audit line
        _write_audit(audit_path, email["id"], "remove_from_docs_missing",
                     {"client_id": row["client_id"], "removed": item,
                      "reason": "same document type received with a matching attachment"})
    return {"ok": True, "message": f"Tracker updated for {row['name']}.", "diff": diff, "removed": removals}


def reject(email: dict, reason: str = "", audit_path=None):
    """Rejecting changes nothing in the tracker; it is only recorded."""
    _write_audit(audit_path or config.AUDIT_LOG_CSV, email["id"], "reject", {"reason": reason})
