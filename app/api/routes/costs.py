"""Cost endpoint: per-call attribution, totals, budget state."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, settings_dep
from app.config import Settings
from app.db.models import CostLog
from app.schemas.api import CostSummary
from app.services import costs

router = APIRouter(tags=["costs"])


@router.get("/costs", response_model=CostSummary)
def cost_summary(db: Session = Depends(get_db), settings: Settings = Depends(settings_dep)):
    return costs.summarize(db, settings)


@router.get("/costs/recent")
def recent_costs(db: Session = Depends(get_db)):
    """Newest 50 cost_log rows — one per AI call, attributed."""
    rows = db.query(CostLog).order_by(CostLog.id.desc()).limit(50).all()
    return [
        {
            "id": row.id,
            "created_at": row.created_at,
            "kind": row.kind,
            "model": row.model,
            "ref": f"{row.ref_table}:{row.ref_id}" if row.ref_table else None,
            "job_id": row.job_id,
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "estimated_cost_usd": row.estimated_cost_usd,
            "latency_ms": row.latency_ms,
        }
        for row in rows
    ]
