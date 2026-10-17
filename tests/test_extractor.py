"""Tests for extractor.py. No real API calls: a fake client stands in for Claude."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import checks
import extractor
from config import EMAILS_DIR


def load_email(email_id):
    return json.loads((EMAILS_DIR / f"{email_id}.json").read_text(encoding="utf-8"))


class FakeClient:
    """Mimics anthropic.Anthropic(): returns canned replies in order and records the calls."""
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.replies.pop(0)
        return type("R", (), {"content": [type("C", (), {"text": text})()]})()


GOOD = json.dumps({"documents_mentioned": [{"type": "W-2", "employer": "Acme", "quote": "my W2 from Acme"}],
                   "life_changes": [], "questions_from_client": []})


def test_llm_uses_temperature_zero_and_saves_raw(tmp_path):
    client = FakeClient([GOOD])
    res = extractor.extract(load_email("E01"), mode="llm", client=client, raw_dir=tmp_path)
    assert res["ok"] and client.calls[0]["temperature"] == 0
    assert (tmp_path / "E01.txt").read_text(encoding="utf-8") == GOOD


def test_malformed_json_is_retried_once(tmp_path):
    client = FakeClient(["Sure! here you go", GOOD])
    res = extractor.extract(load_email("E01"), mode="llm", client=client, raw_dir=tmp_path)
    assert res["ok"] and len(client.calls) == 2
    assert "next attempt" in (tmp_path / "E01.txt").read_text(encoding="utf-8")


def test_two_malformed_replies_give_up(tmp_path):
    client = FakeClient(["nope", "still nope"])
    res = extractor.extract(load_email("E01"), mode="llm", client=client, raw_dir=tmp_path)
    assert not res["ok"] and len(client.calls) == 2 and res["error"]


def test_code_fenced_json_is_accepted(tmp_path):
    client = FakeClient(["```json\n" + GOOD + "\n```"])
    assert extractor.extract(load_email("E01"), mode="llm", client=client, raw_dir=tmp_path)["ok"]


def test_email_body_is_sent_as_data_not_instructions(tmp_path):
    client = FakeClient([GOOD])
    extractor.extract(load_email("E10"), mode="llm", client=client, raw_dir=tmp_path)
    call = client.calls[0]
    assert "<email_body>" in call["messages"][0]["content"]
    assert "UNTRUSTED" in call["system"]


def test_rules_mode_has_no_false_document_on_injection():
    res = extractor.extract(load_email("E10"), mode="rules")
    clean, _ = checks.verify_extraction(res["extraction"], load_email("E10")["body"])
    assert clean["documents_mentioned"] == []


def test_rules_mode_quotes_are_verbatim_for_all_emails():
    """Rules-only quotes are cut from the body, so none should be dropped."""
    for i in range(1, 11):
        email = load_email(f"E{i:02d}")
        res = extractor.extract(email, mode="rules")
        _, log = checks.verify_extraction(res["extraction"], email["body"])
        assert log == [], (email["id"], log)


def test_rules_mode_finds_maria_example():
    email = load_email("E01")
    clean, _ = checks.verify_extraction(extractor.extract(email, mode="rules")["extraction"], email["body"])
    assert clean["documents_mentioned"][0]["employer"] == "Acme"
    assert clean["life_changes"][0]["type"] == "freelance income"
    assert clean["life_changes"][0]["amount_text"] == "about $5k"


def test_temperature_recorded_when_applied(tmp_path):
    res = extractor.extract(load_email("E01"), mode="llm", client=FakeClient([GOOD]), raw_dir=tmp_path)
    assert res["temperature_applied"] is True


def test_temperature_recorded_when_model_refuses_it(tmp_path):
    class Refuses(FakeClient):
        def create(self, **kwargs):
            if "temperature" in kwargs:
                raise Exception("`temperature` is deprecated for this model.")
            return super().create(**kwargs)
    client = Refuses([GOOD])
    res = extractor.extract(load_email("E01"), mode="llm", client=client, raw_dir=tmp_path)
    assert res["ok"] and res["temperature_applied"] is False
    assert "temperature" not in client.calls[0]


def test_rules_mode_has_no_temperature():
    assert extractor.extract(load_email("E01"), mode="rules")["temperature_applied"] is None


def test_thinking_block_before_the_json_is_handled(tmp_path):
    """Newer models can send a 'thinking' block before the text. Only the text block is read."""
    class Think(FakeClient):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            blocks = [type("T", (), {"type": "thinking"})(), type("X", (), {"type": "text", "text": GOOD})()]
            return type("R", (), {"content": blocks})()
    res = extractor.extract(load_email("E01"), mode="llm", client=Think([]), raw_dir=tmp_path)
    assert res["ok"] and res["extraction"]["documents_mentioned"][0]["employer"] == "Acme"
