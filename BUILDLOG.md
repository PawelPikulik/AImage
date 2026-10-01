# BUILDLOG.md — AI-usage log

This capstone was built in collaboration with an AI coding agent (Warp Oz). Per the
ground rules: where AI helped, where it was wrong, what changed. I can explain any
part of this codebase on request.

## Where AI did the work

- **Scaffold + implementation** — the agent generated the full project from the
  capstone brief PDF: FastAPI app, Alembic migration, providers, job engine, guard,
  tests, docs. I directed, reviewed, and verified each phase.
- **Corpus curation** — the agent queried the Wikimedia Commons API, filtered to
  JPEG/usable sizes, recorded license + artist per image, and verified all 46
  downloads by sha256.
- **Debugging** — the agent found and fixed its own bugs (below) via the test suite
  and live API probing.

## Human decisions (mine, not the AI's)

- Stack: Python + FastAPI + Pydantic; Gemini Flash free tier; local build first.
- Eval labels are **subject-level** (`expected_subject: fox`), not exact files — with
  12 valid fox photos, exact-file labels measure luck. I picked this after seeing the
  first offline eval run; the agent implemented the change.
- Wikimedia Commons instead of Unsplash/Pexels for the corpus (keyless, scriptable,
  licensed-free — same constraint, better reproducibility).

## Where the AI was wrong, and what changed

1. **Mock embeddings had hash collisions.** First version mapped each token to one of
   768 dims by hash; wolf captions collided with fox-post tokens (similarity 0.20
   instead of ~0.0 — no subject separation). Caught by an inline sanity check.
   Fix: 3 dims per token, stopword removal, double weight on canonical subjects.
2. **A wrong test expectation.** `test_permanent_failure_stops_without_burning_retries`
   originally asserted an embedding cost row exists after the embedding call *raised*;
   a raised call has no usage to log. Corrected the assertion to the real invariant
   (the successful vision call's cost row survives the failed item).
3. **422 handler could itself 500.** Live probing with a malformed JSON body exposed
   that `RequestValidationError.errors()` embeds raw bytes, which crashed
   `json.dumps` in my custom handler — the exact "500 instead of clean 4xx" bug the
   brief warns about. Fixed by sanitizing error shapes; regression test added.
4. **Exact-file eval set.** The agent's first eval set pinned exact filenames; the
   first offline run scored 0.300 despite every suggestion being the right subject.
   We discussed and switched to subject-level labels (decision recorded above).
5. **Threshold storage moved to the DB.** Initially `config/thresholds.json` only;
   that breaks in multi-container Docker (api vs worker filesystems). Now the tuner
   writes the `app_config` table (authoritative) and the file is just a clone default.

## What I verified myself vs. what you should re-check

I ran the full offline suite (39 passed) and the end-to-end seed + eval + API probe
transcripts pasted in EVIDENCE.md. What I could **not** do without an API key: the
live Gemini run. Before submitting, do the live pass once: add your key to `.env`,
`docker compose up --build`, `docker compose exec api python -m app.seed`, then
`docker compose exec api python -m app.eval` and paste the fresh numbers into
README.md / EVIDENCE.md if they differ.
