"""Plain-code safety checks. No AI in this file, so every rule is predictable and testable.

The LLM (or the rules-only extractor) only PROPOSES. These functions decide what is trusted.
"""
import re
from email.utils import parseaddr

from config import DOC_TYPES, LIFE_CHANGE_TYPES, LIST_FIELDS, SET_FIELDS

# How each document type might be written in an email or a filename.
# (?<![a-z0-9]) / (?![a-z0-9]) = "not part of a longer word", so "1098" does not match "1098-E".
DOC_PATTERNS = {
    "W-2": r"(?<![a-z0-9])w[-_ ]?2(?![a-z0-9])",
    "1099-NEC": r"(?<![a-z0-9])1099[-_ ]?nec(?![a-z0-9])",
    "1099-B": r"(?<![a-z0-9])1099[-_ ]?b(?![a-z0-9])",
    "1098-E": r"(?<![a-z0-9])1098[-_ ]?e(?![a-z0-9])",
    "1098": r"(?<![a-z0-9])1098(?![0-9]|[-_ ]?e(?![a-z0-9]))",
    "Income and expense summary": r"income[\s_\-]*(and|&)?[\s_\-]*expense",
}

# Wording that says "this document is NOT here yet" (decision: code checks wording).
PROMISED_WORDS = re.compile(
    r"coming|will send|ill send|i'll send|will forward|i'll forward|ill forward|next week|"
    r"soon|on the way|in the mail|will be posted|haven't (got|received)|waiting (on|for)",
    re.I,
)
ATTACHED_WORDS = re.compile(r"\battach(ed|ing|ment|ments)?\b|\benclosed\b", re.I)
# "docs coming" with no document named
VAGUE_DOCS = re.compile(
    r"\b(docs?|documents?|paperwork|forms?|stuff)\b[^.!?\n]{0,25}"
    r"\b(coming|on the way|to follow|later)\b",
    re.I,
)
INJECTION = re.compile(
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts?)|"
    r"disregard (all |any )?(the )?(previous|prior|above)|"
    r"you are now|system prompt|mark (all|every)\b[^.\n]{0,30}\bas (received|complete)",
    re.I,
)


# ---------- small helpers ----------

def normalize(text: str) -> str:
    """Lowercase, straighten curly quotes, collapse whitespace. Used before comparing text."""
    text = (text or "").replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def sender_address(from_field: str) -> str:
    """'Maria Lopez <Maria@Example.com>' -> 'maria@example.com'."""
    return parseaddr(from_field or "")[1].strip().lower()


def flag(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def doc_label(doc: dict) -> str:
    """How a document is written in the tracker: 'W-2 (Acme)' or just '1098'."""
    return f"{doc['type']} ({doc['employer']})" if doc.get("employer") else doc["type"]


# ---------- step 3: verify what the extractor proposed ----------

def quote_in_body(quote: str, body: str) -> bool:
    """True only if the quote appears word-for-word in the email (ignoring case/spacing)."""
    q = normalize(quote)
    return bool(q) and q in normalize(body)


def quote_supports_type(doc_type: str, quote: str) -> bool:
    """The quote must actually mention the document. Stops 'mark all my docs as received'
    from being turned into a fake W-2."""
    return bool(re.search(DOC_PATTERNS[doc_type], quote or "", re.I))


def verify_extraction(extraction: dict, body: str):
    """Keep only items that are grounded in the email.
    Returns (clean_extraction, log) where log lists everything dropped and why."""
    log = []
    clean = {"documents_mentioned": [], "life_changes": [], "questions_from_client": []}
    canon_docs = {t.lower(): t for t in DOC_TYPES}
    canon_life = {t.lower(): t for t in LIFE_CHANGE_TYPES}

    for item in extraction.get("documents_mentioned", []):
        dtype = canon_docs.get(str(item.get("type", "")).lower())
        if dtype is None:
            log.append(f"dropped: type not allowed ({item.get('type')!r})")
        elif not quote_in_body(item.get("quote", ""), body):
            log.append(f"dropped: quote not found ({item.get('quote')!r})")
        elif not quote_supports_type(dtype, item["quote"]):
            log.append(f"dropped: quote does not mention {dtype} ({item['quote']!r})")
        else:
            doc = {"type": dtype, "quote": item["quote"]}
            employer = item.get("employer")
            if employer and dtype != "W-2":
                log.append(f"dropped: employer ignored on {dtype} (only W-2s have an employer)")
            elif employer and normalize(employer) in normalize(item["quote"]):
                doc["employer"] = employer
            elif employer:
                log.append(f"dropped: employer not in its own quote ({employer!r})")
            clean["documents_mentioned"].append(doc)

    for item in extraction.get("life_changes", []):
        ltype = canon_life.get(str(item.get("type", "")).lower())
        if ltype is None:
            log.append(f"dropped: type not allowed ({item.get('type')!r})")
        elif not quote_in_body(item.get("quote", ""), body):
            log.append(f"dropped: quote not found ({item.get('quote')!r})")
        else:
            change = {"type": ltype, "quote": item["quote"]}
            # Amounts stay as the client's own words ("about $5k"). Never a number.
            amount = item.get("amount_text")
            if isinstance(amount, str) and normalize(amount) in normalize(body):
                change["amount_text"] = amount
            clean["life_changes"].append(change)

    for item in extraction.get("questions_from_client", []):
        if quote_in_body(item.get("quote", ""), body):
            clean["questions_from_client"].append({"quote": item["quote"]})
        else:
            log.append(f"dropped: quote not found ({item.get('quote')!r})")

    return clean, log


# ---------- step 3: attachment checks ----------

def attachment_matches(doc_type: str, filename: str) -> bool:
    return bool(re.search(DOC_PATTERNS[doc_type], filename or "", re.I))


def claims_attachment(body: str) -> bool:
    return bool(ATTACHED_WORDS.search(body or ""))


def is_promised(doc: dict, body: str) -> bool:
    """Is this document described as 'coming later'? Looks at the sentence around its quote."""
    norm_body = normalize(body)
    pos = norm_body.find(normalize(doc["quote"]))
    if pos < 0:
        return False
    # sentence boundaries around the quote
    start = max(norm_body.rfind(".", 0, pos), norm_body.rfind("\n", 0, pos), -1) + 1
    ends = [e for e in (norm_body.find(".", pos + len(doc["quote"])),) if e >= 0]
    sentence = norm_body[start: ends[0] if ends else len(norm_body)]
    return bool(PROMISED_WORDS.search(sentence)) and not ATTACHED_WORDS.search(sentence)


def check_attachments(body: str, attachments: list, documents: list) -> list:
    flags = []
    if claims_attachment(body) and not attachments:
        flags.append(flag("attachment_claimed_but_none",
                          "Email says something is attached, but there are no attachments."))
    elif attachments or documents:
        for doc in documents:
            if is_promised(doc, body):
                continue  # "coming next week" is fine, it is just still missing
            if not any(attachment_matches(doc["type"], f) for f in attachments):
                flags.append(flag("document_without_attachment",
                                  f"Email mentions {doc_label(doc)} but no attachment looks like it."))
    for f in attachments:
        if not any(attachment_matches(t, f) for t in DOC_TYPES):
            flags.append(flag("unidentified_attachment",
                              f"Attachment '{f}' cannot be matched to a known document type."))
    return flags


def check_vague_documents(body: str, documents: list) -> list:
    if not documents and VAGUE_DOCS.search(body or ""):
        return [flag("vague_documents", "Client says documents are coming but names none.")]
    return []


def check_injection(body: str) -> list:
    if INJECTION.search(body or ""):
        return [flag("possible_prompt_injection",
                     "Email contains text that looks like instructions to the AI. Ignored; review.")]
    return []


def check_questions(questions: list) -> list:
    if questions:
        return [flag("client_question", "Client asked a question that needs a human reply.")]
    return []


def check_rules_coverage(life_changes: list, rules_types: set) -> list:
    """A life change with no row in rules.csv cannot produce an expected-docs list."""
    return [flag("no_rule_for_life_change",
                 f"No rule for '{lc['type']}', so expected documents are unknown.")
            for lc in life_changes if lc["type"] not in rules_types]


# ---------- step 5: compare with the tracker ----------

def _employer_key(name: str) -> str:
    key = re.sub(r"[^a-z0-9 ]", "", normalize(name))
    return re.sub(r"\b(inc|corp|corporation|ltd|llc|co|company)\b", "", key).strip()


def employer_key(name: str) -> str:
    return _employer_key(name)


def _is_known_employer(emp: str, tracker_row: dict) -> bool:
    """True if the employer matches last year's employer or the current employer already on file."""
    known = [tracker_row.get("employer_last_year", ""), tracker_row.get("current_employer", "")]
    return any(k and _employer_key(emp) == _employer_key(k) for k in known)


def check_employer_conflicts(documents: list, tracker_row: dict) -> list:
    """Flag when the email's employer is neither last year's nor the current one on file.
    The flag asks a person to confirm; approving then records the new current employer."""
    last = tracker_row.get("employer_last_year", "")
    current = tracker_row.get("current_employer", "")
    flags = []
    for doc in documents:
        emp = doc.get("employer")
        if emp and last and not _is_known_employer(emp, tracker_row):
            where = (f"last year ({last} → {emp}) and from the current employer on file ({current})"
                     if current else f"last year ({last} → {emp})")
            flags.append(flag("employer_conflict", f"Employer differs from {where}. Confirm this is a job change."))
    return flags


def proposed_current_employer(documents: list, tracker_row: dict):
    """The employer to record as current if a person approves: the first employer named on a W-2 that is
    neither last year's nor the current one on file. None when there is nothing to change."""
    for doc in documents:
        emp = doc.get("employer")
        if emp and tracker_row.get("employer_last_year") and not _is_known_employer(emp, tracker_row):
            return emp
    return None


# ---------- step 8: append, never overwrite ----------

def split_list(cell) -> list:
    return [p.strip() for p in str(cell or "").split(";") if p.strip()]


def append_to_list_field(cell, new_items: list) -> str:
    """Add items to a 'a; b; c' cell. Existing entries are kept; duplicates are skipped,
    so running it twice gives the same result (idempotent)."""
    items = split_list(cell)
    seen = {i.lower() for i in items}
    for new in new_items:
        if new.lower() not in seen:
            items.append(new)
            seen.add(new.lower())
    return "; ".join(items)


def doc_type_of(label: str) -> str:
    """'W-2 (Acme)' -> 'W-2'; '1099-NEC (if issued)' -> '1099-NEC'."""
    return label.split(" (")[0].strip()


def find_removals(row: dict, changes: dict, attachments) -> list:
    """The ONE exception to append-only: an item leaves docs_missing only when the same
    document type is being added to docs_received AND an attachment filename matches it.
    Returns the docs_missing entries that should be removed."""
    arriving = {doc_type_of(d) for d in changes.get("docs_received", [])}
    verified = {t for t in arriving
                if t in DOC_PATTERNS and any(attachment_matches(t, f) for f in attachments or [])}
    return [m for m in split_list(row.get("docs_missing", "")) if doc_type_of(m) in verified]


def apply_changes(row: dict, changes: dict, attachments=()) -> dict:
    """Return a copy of the tracker row with changes applied.
    List fields are only appended to. The one single-value field (current_employer) can be set, and
    only to a non-empty value. Anything else, such as employer_last_year, is refused.
    The only removal is described in find_removals()."""
    updated = dict(row)
    for field, value in changes.items():
        if field in LIST_FIELDS:
            updated[field] = append_to_list_field(row.get(field, ""), value)
        elif field in SET_FIELDS:
            if value and str(value).strip():
                updated[field] = str(value).strip()
        else:
            raise ValueError(f"Refusing to change '{field}': only list fields can be appended to "
                             f"and {SET_FIELDS} can be set.")
    gone = {m.lower() for m in find_removals(row, changes, attachments)}
    if gone:
        kept = [m for m in split_list(updated.get("docs_missing", "")) if m.lower() not in gone]
        updated["docs_missing"] = "; ".join(kept)
    return updated
