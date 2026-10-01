"""Tune the guard's similarity threshold against the labeled eval set.

Sweeps candidate thresholds over data/eval_set.json using the *current*
embeddings in the database, then persists the best one to the app_config table
(runtime-authoritative, shared by api + worker) and config/thresholds.json
(repo provenance). The threshold is chosen from data, not guessed — the seed
script runs this automatically after processing; run it again by hand after
re-tagging with `force`.

Usage:  python scripts/tune_thresholds.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.services import tuning  # noqa: E402


def main() -> int:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        best = tuning.tune(session, settings)
        session.commit()
    print(
        f"best threshold {best['threshold']:.2f} → "
        f"top-1 precision {best['top1_precision']:.3f}, "
        f"rejection accuracy {best['rejection_accuracy']:.3f} "
        f"({best['labeled_posts']} labeled + {best['rejection_posts']} rejection posts)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
