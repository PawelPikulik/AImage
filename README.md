# flyrank-capstone-image-relevance

**AI Image Understanding & Content Matching Engine** — FlyRank Internship, Backend Track capstone.

A service that looks at an image library, understands what is actually in each image
(structured, schema-validated tags — not filenames), and matches the right image to the
right blog post by meaning. A red-fox post gets a red-fox photo; the similar-looking wolf
is **rejected with an explanation**; and when nothing clears the bar, the system says
"no confident match" instead of guessing.

## Evaluation headline numbers

Measured by `python -m app.eval` against the labeled eval set (`data/eval_set.json`,
10 subject-labeled posts + 3 posts that must be rejected):

| Metric | Value |
|---|---|
| Top-1 precision | **1.000** (10/10) |
| Rejection accuracy | **1.000** (3/3) |

Provenance: the committed `config/thresholds.json` (match threshold 0.20, confidence floor
0.70) was tuned by `scripts/tune_thresholds.py` on the eval set. `python -m app.seed`
re-tunes automatically after every processing run, so the number above is reproduced by
the seed → eval sequence. See EVIDENCE.md for transcripts.

## Architecture

```
Images ─(batch job)→ Vision model → {tags, caption, confidence} ─validate→ images
                          └ embed(caption) ─────────────────────→ image vectors
Posts ──(batch job)→ subject extraction + embed(post text) ─────→ post vectors

GET /posts/:id/images
    → Similarity ranking (pgvector cosine, HNSW)
    → Mismatch guard (subject conflict → confidence floor → similarity threshold)
        ├─ Suggested image (ranked, explained)
        └─ "No confident match" + reasons
    → Review API: approve / reject
```

Layered: `app/api` (HTTP, Pydantic-validated) → `app/services` (vision, embeddings,
matching, guard, jobs, costs, tuning) → `app/db` (SQLAlchemy + Alembic). AI access goes
through `app/providers` (`GeminiProvider`, or `MockProvider` for tests/offline dev).

The mismatch guard (`app/services/guard.py`) rejects a candidate when, in order:
1. the post clearly names a subject and the image is a **different sibling subject**
   (fox post vs wolf image → `Animal category mismatch: expected fox, detected wolf`;
   aliases make `Vulpes vulpes` ≡ `red fox`);
2. the image's vision confidence fell below the floor at ingestion (flagged, not trusted);
3. cosine similarity is below the tuned threshold (`config/thresholds.json` / DB).

## Live provider status (2026-10-01)

A Google AI Studio key has been provisioned and configured by the maintainer in
`.env` (gitignored, never committed). A live smoke run against real Gemini tagged
6 corpus images with genuine model output (e.g. fox-01 → `subject: "red fox",
confidence: 0.98`, caption *"A close-up portrait of a fluffy red fox looking
directly at the camera…"*) and logged real token counts per call — see
EVIDENCE.md § "Live provider smoke run". The full 46-image live run is deferred:
the free tier caps `gemini-3.8-flash` at **20 generate-content requests/day**
(verified from the 429 quota body), so the defaults now use `gemini-3.7-flash`.
The seed is resumable across models/days — re-running it skips tagged images.

## Run it (Docker, one command)

Prereqs: Docker, and a free Gemini key from <https://aistudio.google.com/apikey> (no card).
Note: free-tier daily caps differ per model; `gemini-3.7-flash` is the default
because it served live vision calls at build time (see `.env.example` comments).

```bash
cp .env.example .env          # paste your GEMINI_API_KEY
python scripts/download_corpus.py   # 46 freely-licensed images (Wikimedia Commons)
docker compose up --build     # db (pgvector) + api + worker
```

Seed (idempotent — registers images+posts, runs the batch jobs, tunes the guard):

```bash
docker compose exec api python -m app.seed
```

Then:

```bash
curl http://localhost:8000/posts                     # find a post id
curl http://localhost:8000/posts/1/images            # ranked suggestions + guard verdicts
curl -X POST http://localhost:8000/posts/1/guard-check \
  -H 'Content-Type: application/json' -d '{"image_id": 13}'   # force the wolf: REJECTED
docker compose exec api python -m app.eval           # top-1 precision on the eval set
```

**No key / offline demo:** set `MOCK_AI=true` in `.env` — a deterministic stand-in
provider exercises the full pipeline (schema validation, batch jobs, guard, ranking)
with zero API calls. Tests always run this way:

```bash
pip install -r requirements.txt
pytest
```

## API surface

| Endpoint | Purpose |
|---|---|
| `POST /jobs/image-processing` | enqueue batch vision tagging (idempotent; `{"force": true}` re-tags) |
| `GET /jobs/{id}` | job progress (total/done/failed) |
| `POST /posts` | create post → async subject extraction + embedding |
| `GET /posts/{id}/images` | ranked candidates, guard verdicts, suggestion or `no_confident_match` |
| `POST /posts/{id}/guard-check` | force one image through the guard with explanation |
| `GET /suggestions?status=…` | review queue (verdict + reasons preserved per row) |
| `POST /suggestions/{id}/approve` / `…/reject` | human review (idempotent) |
| `GET /images?flagged=true` | low-confidence images flagged at ingestion |
| `GET /costs`, `GET /costs/recent` | per-call cost attribution + budget state |

## Design decisions worth knowing

- **Never trust the model's shape.** Every vision/extraction response is validated
  against a Pydantic schema (`app/schemas/tags.py`); invalid output raises
  `SchemaValidationError`, the job item is retried, and nothing is persisted.
- **Batch discipline.** Vision/embedding calls run as DB-backed jobs (`jobs` +
  `job_items`) with exponential-backoff retries, a failure log alert after the attempt
  cap, and idempotent enqueue (`UNIQUE(job_id, ref_id)`).
- **Cost is a habit.** Every AI call writes a `cost_log` row (tokens, estimated USD at
  list prices in `config/cost_rates.json` — free tier bills $0); `COST_BUDGET_USD`
  guards new jobs.
- **The corpus is reproducible, not committed.** `data/corpus_manifest.json` pins 46
  Wikimedia Commons images (license + artist + sha256 per entry);
  `scripts/download_corpus.py` re-fetches and verifies them.
  (The brief suggests Unsplash/Pexels; Commons was chosen because its keyless API makes
  the corpus reproducible by anyone with one command — same licensed-free requirement.)
- **Eval labels are subject-level.** With 12 equally-correct fox photos, pinning one
  exact file per post would measure luck; `expected_subject: fox` measures the ranking.
- **Thresholds live in the DB** (`app_config` table, written by the tuner during seed)
  so api and worker containers share them and they survive restarts; env
  `MATCH_THRESHOLD` overrides, `config/thresholds.json` is the fresh-clone default.

## Limitations (honest)

- The subject-conflict check relies on a small canonical taxonomy
  (`app/services/textnorm.py`). Subjects outside it fall back to the similarity
  threshold alone — fine at ~50 images, incomplete at 50k.
- Gemini's self-reported `confidence` is not calibrated; the floor is a heuristic.
- The job queue is a DB poll loop — right-sized for this scale; not Kafka.
- `MockProvider` is a development/test stand-in. Its "vision" is filename-derived and
  its embeddings are lexical, so offline precision (1.000) overstates semantic quality.
  The real number comes from a live Gemini run: `MOCK_AI=false python -m app.seed &&
  python -m app.eval` (docker compose equivalent above), then update the table above.
- The guard's subject taxonomy is alias-driven; the live model answers with
  breeds/subspecies (observed: "Mexican wolf", "Cane Corso", "red fox kits").
  Common ones are mapped in `app/services/textnorm.py`; unmapped subjects are
  conservatively rejected as "subject unverifiable" rather than guessed.
