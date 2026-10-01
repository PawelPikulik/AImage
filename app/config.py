"""Runtime configuration.

Everything comes from environment variables (see .env.example); secrets are
never hard-coded. `get_settings` is cached so modules can import it freely.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS_PATH = ROOT / "config" / "thresholds.json"
COST_RATES_PATH = ROOT / "config" / "cost_rates.json"
CORPUS_DIR = ROOT / "data" / "corpus"
MANIFEST_PATH = ROOT / "data" / "corpus_manifest.json"
POSTS_SEED_PATH = ROOT / "data" / "posts_seed.json"
EVAL_SET_PATH = ROOT / "data" / "eval_set.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/imagematch"

    gemini_api_key: str = ""
    # Model availability and free-tier daily caps vary per model and change over
    # time (verified live 2026-10-01: 2.5-flash closed to new keys; 3.8-flash free
    # tier = 20 generate-content requests/day). 3.7-flash served real vision calls.
    # The seed is resumable — if your key hits a daily cap mid-run, switch the
    # model here/in .env and re-run; already-tagged rows are skipped.
    gemini_vision_model: str = "gemini-3.7-flash"
    gemini_text_model: str = "gemini-3.7-flash"
    gemini_embed_model: str = "gemini-embedding-001"
    embed_dims: int = 768

    mock_ai: bool = False

    corpus_dir: str = str(CORPUS_DIR)
    confidence_floor: float = 0.70
    match_threshold: str = ""  # empty → read config/thresholds.json
    cost_budget_usd: float = 5.00

    worker_poll_interval_seconds: float = 2.0
    job_max_attempts: int = 4
    job_retry_base_seconds: float = 2.0
    job_item_pace_seconds: float = 0.0  # >0 paces job items (free-tier rate limits)

    ranking_candidates: int = 15  # > 12 foxes so the top rejected candidate is visible inline


def load_thresholds() -> dict:
    return json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))


def load_cost_rates() -> dict:
    return json.loads(COST_RATES_PATH.read_text(encoding="utf-8"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
