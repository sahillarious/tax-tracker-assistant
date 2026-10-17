"""Draft every email that is allowed a reply, in AI mode, and report how many drafts passed the
plain-code verifier and how many fell back to the template (and why).

    python draft_eval.py

Needs ANTHROPIC_API_KEY (in .env). Each run makes a few dozen real API calls.
"""
import sys

import config
import extractor
import pipeline
import reply


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if extractor.active_mode() != "llm":
        print("No ANTHROPIC_API_KEY set (put it in .env). Nothing to run.")
        return
    # Always the PRISTINE tracker, so entries typed in the app cannot change the result.
    tracker, rules = pipeline.load_tracker(config.TRACKER_ORIGINAL_CSV), pipeline.load_rules()
    rows, failed, counts = [], [], {"ai": 0, "template": 0, "none": 0}
    for eid in pipeline.list_email_ids():
        email = pipeline.load_email(eid)
        res = pipeline.process_email(email, mode="llm", tracker=tracker, rules=rules)
        d = reply.make_draft(res, email, mode="llm", rules=rules)
        counts[d["source"]] += 1
        if d["source"] == "none":
            why = d["note"]
        elif d["source"] == "ai":
            why = f"{len(d['body'].split())} words"
        else:
            why = d["note"] + (": " + "; ".join(d["problems"]) if d["problems"] else "")
        rows.append((eid, d["source"], why))
        if d["failed_draft"]:
            failed.append((eid, d["failed_draft"]))
    print(f"{'Email':<6}{'Draft':<10}Detail")
    print("-" * 100)
    for eid, source, why in rows:
        label = {"ai": "AI ok", "template": "TEMPLATE", "none": "no draft"}[source]
        print(f"{eid:<6}{label:<10}{why}")
    print("-" * 100)
    drafted = counts["ai"] + counts["template"]
    print(f"Emails allowed a draft: {drafted} of {len(rows)}")
    print(f"  passed the verifier (AI draft used): {counts['ai']}")
    print(f"  fell back to the template:           {counts['template']}")
    print(f"  no draft (a person must write it):   {counts['none']}")
    for eid, text in failed:  # show exactly what the model wrote when a check rejected it
        print("\n--- rejected AI draft for " + eid + " ---\n" + text)


if __name__ == "__main__":
    main()

