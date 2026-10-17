"""Shared test setup: every test runs against a pristine COPY of the tracker.

Without this, tests read the live data/tracker.csv, so approving emails in the app (or typing manual
entries) would change what the tests see, and a test could also change the live file. The folder for
raw model replies is redirected too, so tests never leave files in data/raw_llm."""
import shutil

import pytest

import config


@pytest.fixture(autouse=True)
def pristine_data(tmp_path, monkeypatch):
    tracker, audit = tmp_path / "tracker.csv", tmp_path / "audit_log.csv"
    shutil.copy(config.TRACKER_ORIGINAL_CSV, tracker)
    monkeypatch.setattr(config, "TRACKER_CSV", tracker)
    monkeypatch.setattr(config, "AUDIT_LOG_CSV", audit)
    monkeypatch.setattr(config, "RAW_LLM_DIR", tmp_path / "raw_llm")
    return tracker, audit
