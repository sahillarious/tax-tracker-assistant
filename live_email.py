"""Typed-live emails: a person types an email into the app and runs it through the same pipeline.

These emails exist only in the browser session. Nothing is written to data/emails, and the
evaluation and tests (which read data/emails) never see them. If someone approves or rejects one,
the audit log records it under its LIVE id like any other email.
"""
import re

import checks
import config
import pipeline

PREFIX = "LIVE-"
NOT_IN_TRACKER = "Someone not in the tracker"
STRANGER = "Someone Else <someone.else@example.com>"   # an address that is not in the tracker


def sender_options(tracker: list) -> list:
    """Dropdown entries: every client in the tracker, then the 'not in the tracker' choice."""
    return [f"{r['name']} ({r['email']})" for r in tracker] + [NOT_IN_TRACKER]


def sender_header(choice: str, tracker: list) -> str:
    """Turn a dropdown choice into a 'From' header such as 'Maria Lopez <maria@example.com>'."""
    if choice == NOT_IN_TRACKER:
        return STRANGER
    for r in tracker:
        if choice == f"{r['name']} ({r['email']})":
            return f"{r['name']} <{r['email']}>"
    raise ValueError(f"Unknown sender choice: {choice!r}")


def parse_attachments(text: str) -> list:
    """'a.pdf, b.pdf' -> ['a.pdf', 'b.pdf'] (blank entries are dropped)."""
    return [p.strip() for p in (text or "").split(",") if p.strip()]


def next_live_id(session_ids, audit_path=None) -> str:
    """LIVE-1, LIVE-2 ... Numbers already used in the audit log are skipped, so a new session can never
    inherit an old approval (approve is once-per-id) and ids stay unique until the demo is reset."""
    used = [int(m.group(1)) for i in session_ids if (m := re.fullmatch(r"LIVE-(\d+)", i))]
    path = audit_path or config.AUDIT_LOG_CSV
    if path.exists():
        used += [int(m.group(1)) for r in pipeline._read_audit(path)
                 if (m := re.fullmatch(r"LIVE-(\d+)", r["email_id"]))]
    return f"{PREFIX}{max(used, default=0) + 1}"


def build_email(live_id: str, sender_choice: str, subject: str, body: str,
                attachments_text: str, tracker: list) -> dict:
    return {"id": live_id, "from": sender_header(sender_choice, tracker), "subject": (subject or "").strip(),
            "body": body or "", "attachments": parse_attachments(attachments_text)}


def is_empty(email: dict) -> bool:
    return not email["body"].strip() and not email["attachments"]


def process_live(email: dict, mode: str, **kwargs) -> dict:
    """Run the same process_email as every other email, plus one guard for an email with no text and no
    attachments: there is nothing to read (so no AI call is made) and a person must look at it."""
    if not is_empty(email):
        return pipeline.process_email(email, mode=mode, **kwargs)
    result = pipeline.process_email(email, mode="rules", **kwargs)
    if result["client"] is not None:   # an unknown sender is already sent to review by the pipeline
        flag = checks.flag("empty_email", "The email has no text and no attachments, so there is nothing to read.")
        result["flags"].append(flag)
        result["reasons"].append(flag["message"])
        result["status"] = "needs review"
    return result
