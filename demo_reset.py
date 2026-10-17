"""Demo helper: put the tracker back to its original state. Used by the "Reset demo data" button in the header.
Kept separate from pipeline.py so the pipeline logic is untouched."""
import shutil

import config


def reset_demo_data(tracker=None, original=None, audit=None):
    """Restore tracker.csv byte-for-byte from the pristine copy and delete the audit log."""
    shutil.copyfile(original or config.TRACKER_ORIGINAL_CSV, tracker or config.TRACKER_CSV)
    (audit or config.AUDIT_LOG_CSV).unlink(missing_ok=True)
