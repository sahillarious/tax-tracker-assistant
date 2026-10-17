"""Settings in one place. Change the model name here, nowhere else."""
from pathlib import Path

try:  # read ANTHROPIC_API_KEY from a local .env file if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# --- LLM settings (only used when ANTHROPIC_API_KEY is set) ---
MODEL_NAME = "claude-sonnet-5-5"
MAX_TOKENS = 1500

# --- Demo constants used in reply drafts (all invented) ---
FIRM_NAME = "Pinecrest Tax Partners (demo firm)"
SIGNER_NAME = "Sarah Whitfield"
REPLY_METHOD = "reply to this email"

# --- File locations (all data is FAKE: example.com addresses, invented names) ---
ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
EMAILS_DIR = DATA_DIR / "emails"
TRACKER_CSV = DATA_DIR / "tracker.csv"
TRACKER_ORIGINAL_CSV = DATA_DIR / "tracker.original.csv"  # pristine copy used by "Reset demo data"
RULES_CSV = DATA_DIR / "rules.csv"
EXPECTED_JSON = DATA_DIR / "expected.json"
AUDIT_LOG_CSV = DATA_DIR / "audit_log.csv"
RAW_LLM_DIR = DATA_DIR / "raw_llm"  # raw model output saved per email, for auditing

# --- Allowed vocabularies: the extractor may only use these labels ---
DOC_TYPES = ["W-2", "1099-NEC", "1099-B", "1098", "1098-E", "Income and expense summary"]
LIFE_CHANGE_TYPES = [
    "freelance income", "sold stock", "new employer",
    "mortgage", "student loan interest", "marriage",
]

# Tracker columns that hold lists (stored as "a; b; c"). Approval may only APPEND to these.
LIST_FIELDS = ["docs_received", "docs_missing", "life_changes"]
# The only single-value column approval may change: the client's CURRENT employer. A person approving a
# job change sets it, and the audit log records the old and new value. employer_last_year is history and
# is never changed.
SET_FIELDS = ["current_employer"]
