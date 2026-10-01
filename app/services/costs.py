"""Per-call cost tracking + budget guard.

Every AI call writes one cost_log row with token counts and an estimated USD
cost computed from config/cost_rates.json (published list prices — the Gemini
free tier bills $0, but the tracking habit is the deliverable). New batch jobs
are refused once the logged spend crosses COST_BUDGET_USD.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import Settings, load_cost_rates
from app.db.models import CostLog
from app.providers.base import CallUsage


class BudgetExceededError(Exception):
    """Raised when a new job would exceed the configured estimated-cost budget."""


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = load_cost_rates()
    rate = rates.get(model, rates["default"])
    return (
        prompt_tokens * rate["input_per_mtok"] + completion_tokens * rate["output_per_mtok"]
    ) / 1_000_000


def record_cost(
    session: Session,
    *,
    kind: str,
    usage: CallUsage,
    ref_table: str | None = None,
    ref_id: int | None = None,
    job_id: int | None = None,
) -> CostLog:
    entry = CostLog(
        kind=kind,
        model=usage.model,
        ref_table=ref_table,
        ref_id=ref_id,
        job_id=job_id,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        estimated_cost_usd=estimate_cost_usd(usage.model, usage.prompt_tokens, usage.completion_tokens),
        latency_ms=usage.latency_ms,
    )
    session.add(entry)
    return entry


def total_estimated_usd(session: Session) -> float:
    return float(session.query(func.coalesce(func.sum(CostLog.estimated_cost_usd), 0.0)).scalar())


def check_budget(session: Session, settings: Settings) -> None:
    spent = total_estimated_usd(session)
    if spent >= settings.cost_budget_usd:
        raise BudgetExceededError(
            f"Estimated AI spend ${spent:.4f} has reached the budget "
            f"(${settings.cost_budget_usd:.2f}). Raise COST_BUDGET_USD to continue."
        )


def summarize(session: Session, settings: Settings) -> dict:
    rows = (
        session.query(
            CostLog.kind,
            func.count(CostLog.id),
            func.coalesce(func.sum(CostLog.prompt_tokens), 0),
            func.coalesce(func.sum(CostLog.completion_tokens), 0),
            func.coalesce(func.sum(CostLog.estimated_cost_usd), 0.0),
        )
        .group_by(CostLog.kind)
        .all()
    )
    by_kind = {
        kind: {
            "calls": calls,
            "prompt_tokens": int(prompt),
            "completion_tokens": int(completion),
            "estimated_cost_usd": round(float(usd), 6),
        }
        for kind, calls, prompt, completion, usd in rows
    }
    total = total_estimated_usd(session)
    return {
        "total_calls": sum(v["calls"] for v in by_kind.values()),
        "total_estimated_cost_usd": round(total, 6),
        "budget_usd": settings.cost_budget_usd,
        "budget_remaining_usd": round(max(settings.cost_budget_usd - total, 0.0), 6),
        "by_kind": by_kind,
    }
