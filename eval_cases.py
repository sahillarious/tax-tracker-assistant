"""Run all 10 sample emails and compare with data/expected.json.

    python eval_cases.py            # every mode that is available (rules-only always; LLM if a key is set)
    python eval_cases.py rules      # one mode only
"""
import json
import sys

import config
import extractor
import pipeline


def compare(result: dict, exp: dict):
    """Return '' if everything matches, else a one-line reason for the first difference."""
    checks = [
        ("status", result["status"], exp["expected_status"]),
        ("flags", sorted(f["code"] for f in result["flags"]), sorted(exp["expected_flags"])),
        ("documents", sorted(d["type"] for d in result["extraction"]["documents_mentioned"]),
         sorted(exp["expected_documents"])),
        ("life changes", sorted(c["type"] for c in result["extraction"]["life_changes"]),
         sorted(exp["expected_life_changes"])),
        ("docs missing", sorted(result["changes"]["docs_missing"]), sorted(exp["expected_docs_missing"])),
    ]
    for name, actual, expected in checks:
        if actual != expected:
            return f"{name}: got {actual}, expected {expected}"
    return ""


def run_mode(mode: str):
    expected = json.loads(config.EXPECTED_JSON.read_text(encoding="utf-8"))
    # Always the PRISTINE tracker: expected.json assumes it, so approvals made in the app must not change the grades.
    tracker, rules = pipeline.load_tracker(config.TRACKER_ORIGINAL_CSV), pipeline.load_rules()
    rows = []
    for eid in pipeline.list_email_ids():
        exp = expected[eid]
        res = pipeline.process_email(pipeline.load_email(eid), mode=mode, tracker=tracker, rules=rules)
        reason = compare(res, exp)
        rows.append((eid, exp["level"], exp["expected_status"], res["status"], not reason, reason))
    return rows


def print_table(mode: str, rows: list):
    label = "LLM mode" if mode == "llm" else "RULES-ONLY mode (no AI)"
    print(f"\n=== {label} ===")
    print(f"{'Email':<6}{'Level':<7}{'Expected':<17}{'Actual':<17}{'Result':<7}Reason")
    print("-" * 100)
    for eid, level, exp, act, ok, reason in rows:
        print(f"{eid:<6}{level:<7}{exp:<17}{act:<17}{'PASS' if ok else 'FAIL':<7}{reason}")
    print("-" * 100)
    for level in (1, 2, 3):
        sub = [r for r in rows if r[1] == level]
        if sub:
            p = sum(r[4] for r in sub)
            print(f"Level {level}: {p}/{len(sub)} passed ({100 * p // len(sub)}%)")
    p = sum(r[4] for r in rows)
    print(f"Overall: {p}/{len(rows)} passed ({100 * p // len(rows)}%)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles can choke on arrows
    wanted = sys.argv[1:] or ["rules", "llm"]
    for mode in wanted:
        if mode == "llm" and extractor.active_mode() != "llm":
            print("\n=== LLM mode ===\nSkipped: no ANTHROPIC_API_KEY set (put it in .env).")
            continue
        print_table(mode, run_mode(mode))
