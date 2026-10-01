# Design — AI Image Understanding & Content Matching Engine

## Problem
Given a ~46-image library and a set of blog posts, understand each image
(structured tags + caption + confidence, schema-validated), and for each post
rank the right images by meaning. The headline feature is the **mismatch
guard**: a fox post must surface a fox photo, a wolf must rank lower and be
rejected if forced, and when nothing is good enough the system says
"no confident match" with reasons instead of guessing.

## Data model (PostgreSQL 16 + pgvector)
- `images(id, filename, sha256 UNIQUE, category, status, subject, tag_category,
  attributes JSONB, caption, confidence, flagged_low_confidence, embedding vector(768))`
- `posts(id, title, body, subjects JSONB, topic_category, status, embedding vector(768))`
- `suggestions(id, post_id FK, image_id FK, rank, similarity, guard_status,
  guard_reasons JSONB, review_status, review_reason, UNIQUE(post_id, image_id))`
  — the unique pair makes re-ranking idempotent.
- `jobs(id, kind, status, total, done, failed, error, created_at, updated_at)`
- `job_items(id, job_id FK, ref_table, ref_id, status, attempts, last_error,
  run_after, UNIQUE(job_id, ref_id))` — retries never duplicate work.
- `cost_log(id, created_at, kind, model, ref_table, ref_id, job_id,
  prompt_tokens, completion_tokens, estimated_cost_usd, latency_ms)`

Indexes: FK columns, `images.status`, `images.tag_category`, `suggestions.review_status`,
`job_items(status, run_after)`, HNSW cosine indexes on both embedding columns.

## API surface
- `POST /jobs/image-processing` → enqueue batch vision job · `GET /jobs/{id}` → progress
- `POST /posts` (validated) → enqueue subject-extraction + embedding job · `GET /posts`
- `GET /posts/{id}/images` → ranked candidates, per-candidate guard verdicts +
  reasons, top suggestion or `"no_confident_match"` with reasons
- `POST /suggestions/{id}/approve` · `POST /suggestions/{id}/reject` ·
  `GET /suggestions?status=pending` — human review workflow
- `GET /costs` — per-call cost attribution + budget state · `GET /healthz`

## Layer sketch
```
HTTP (app/api)         FastAPI routers — Pydantic validation in/out, 4xx never 500
Logic (app/services)   vision · embeddings · matching · guard · jobs · costs
Data (app/db)          SQLAlchemy models · Alembic migrations · pgvector
Providers (app/providers)  AIProvider ABC — GeminiProvider | MockProvider (tests/dev)
```

```
Images ─(batch job)→ Vision model → {tags, caption, confidence} ─validate→ images
                          └ embed(caption) ───────────────→ image vectors
Posts ──(batch job)→ subject extraction + embed(post text) ─→ post vectors
GET /posts/:id/images
    → Similarity ranking (pgvector cosine)
    → Mismatch guard (confidence floor · threshold · subject conflict)
        ├─ Suggested image (ranked, explained)
        └─ "No confident match" + reasons
    → Review API: approve / reject
```

## Mismatch guard rules (evaluated per candidate, in order)
1. Vision confidence below `CONFIDENCE_FLOOR` → rejected ("low vision confidence").
2. Cosine similarity below tuned threshold (`config/thresholds.json`) →
   rejected ("similarity below threshold").
3. Post names a canonical subject and the image's subject is a *different*
   sibling subject (fox vs wolf via alias map incl. "Vulpes vulpes"→fox) →
   rejected ("category mismatch: expected fox, detected wolf").
4. Otherwise accepted. No accepted candidate → "no confident match" + reasons.

## Explicit non-goal
No user-facing frontend. The review workflow is exercised through the API
(`GET /suggestions`, approve/reject endpoints) — a UI is intentionally out of
scope per Section 7 of the brief.
