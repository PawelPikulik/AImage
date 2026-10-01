"""Cost tracking: every AI call is attributed, and the budget guard refuses
new batch jobs once the estimated spend crosses the configured budget."""

from __future__ import annotations

import pytest

from app.db.models import CostLog, Image
from app.services import costs, jobs


class TestPerCallAttribution:
    def test_every_call_logged_with_tokens_and_cost(self, seeded_db, settings):
        rows = seeded_db.query(CostLog).all()
        # 4 images × (vision + embedding) + 2 posts × (text + embedding) = 12
        assert len(rows) == 12
        for row in rows:
            assert row.model
            assert row.ref_table in ("images", "posts")
            assert row.ref_id is not None
            assert row.job_id is not None
            assert row.prompt_tokens > 0
        summary = costs.summarize(seeded_db, settings)
        assert summary["total_calls"] == 12
        assert set(summary["by_kind"]) == {"vision", "embedding", "text"}
        assert summary["total_estimated_cost_usd"] > 0


class TestBudgetGuard:
    def test_budget_exceeded_refuses_new_jobs(self, db, settings):
        settings.cost_budget_usd = 0.000001  # one mock vision call already exceeds it
        costs.record_cost(
            db,
            kind="vision",
            usage=_usage(),
            ref_table="images",
            ref_id=1,
        )
        db.flush()
        with pytest.raises(costs.BudgetExceededError):
            jobs.enqueue_job(db, settings, jobs.KIND_IMAGE, [1])

    def test_under_budget_allows_jobs(self, db, settings):
        job = jobs.enqueue_job(db, settings, jobs.KIND_IMAGE, [1])
        assert job.total == 1


def _usage():
    from app.providers.base import CallUsage

    return CallUsage(model="mock-vision", prompt_tokens=500, completion_tokens=100)
