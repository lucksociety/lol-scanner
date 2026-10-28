"""Runtime configuration. Everything comes from environment variables; nothing secret is committed."""
import os
from pathlib import Path

ROOT = Path(os.environ.get("LOL_SCANNER_ROOT", Path(__file__).resolve().parent.parent))

# LoL Esports "persisted gateway" (the API behind lolesports.com). It requires an x-api-key header.
# The key is NEVER committed: set LOLESPORTS_API_KEY in your shell / .env, or as a GitHub Actions secret.
LOLESPORTS_BASE = os.environ.get("LOLESPORTS_BASE", "https://esports-api.lolesports.com/persisted/gw")
LOLESPORTS_API_KEY = os.environ.get("LOLESPORTS_API_KEY", "")

# Jupiter prediction markets (used ONLY by scanner/edge.py, after the forecast is frozen).
JUP_BASE = os.environ.get("JUP_BASE", "https://prediction-market-api.jup.ag/api/v1")
JUP_API_KEY = os.environ.get("JUP_API_KEY", "")          # optional; only needed for api.jup.ag/prediction/v1
JUP_FEE_COEF = float(os.environ.get("JUP_FEE_COEF", "0.07"))  # taker fee = coef * p * (1-p) per $1 contract

SIMS = int(os.environ.get("LOL_SIMS", "100000"))
SEED = int(os.environ.get("LOL_SEED", "20261028"))
HISTORY_START = os.environ.get("LOL_HISTORY_START", "2025-01-01")
TZ = os.environ.get("LOL_TZ", "America/New_York")

FORECAST_DIR = ROOT / "forecast_log"
ODDS_DIR = ROOT / "odds_snapshots"
EDGE_DIR = ROOT / "edge_log"
CACHE_DIR = ROOT / ".cache"
DOCS_DIR = ROOT / "docs"
CALIB_FILE = ROOT / "calibration" / "calibration_log.csv"

MODEL_VERSION = os.environ.get("LOL_SCANNER_MODEL", "v2")   # primary model for edge/dashboard/grade ("v1" or "v2")
