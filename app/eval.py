"""Evaluation — `python -m app.eval`.

Runs the same ranking + mismatch-guard path the API uses against the labeled
eval set (data/eval_set.json) and reports:

- top-1 precision: share of labeled posts whose suggested image has the
  labeled canonical subject (subject-level labels, because the corpus holds
  several equally-correct images per subject) — the headline number in README.
- rejection accuracy: share of rejection-expected posts correctly answered
  with "no confident match".
"""

from __future__ import annotations

import json
import sys

from app.config import EVAL_SET_PATH, get_settings
from app.db.models import Post
from app.db.session import get_session_factory
from app.services import suggestions, tuning


def main() -> int:
    settings = get_settings()
    entries = json.loads(EVAL_SET_PATH.read_text(encoding="utf-8"))["entries"]

    session_factory = get_session_factory()
    hits = misses = rej_pass = rej_fail = 0
    with session_factory() as session:
        threshold = suggestions.resolve_match_threshold(session, settings)
        print(f"(guard threshold: {threshold:.2f}, confidence floor: {settings.confidence_floor:.2f})\n")
        for entry in entries:
            post = session.query(Post).filter(Post.title == entry["post_title"]).one_or_none()
            if post is None:
                print(f"SKIP  {entry['post_title']!r} — post not seeded")
                continue
            if post.embedding is None:
                print(f"SKIP  {entry['post_title']!r} — post not processed (run the seed/worker)")
                continue
            result = suggestions.suggest_for_post(session, settings, post)
            suggested = result["suggestion"]
            if entry.get("expected_rejection"):
                if result["result"] == "no_confident_match":
                    rej_pass += 1
                    print(f"PASS  {entry['post_title']!r} → correctly answered 'no confident match'")
                else:
                    rej_fail += 1
                    print(
                        f"FAIL  {entry['post_title']!r} → suggested {suggested['filename']} "
                        f"but no suitable image exists"
                    )
                continue
            expected = entry["expected_subject"]
            got_subject = tuning._canonical_of_subject(suggested["subject"]) if suggested else None
            if suggested and got_subject == expected:
                hits += 1
                print(
                    f"PASS  {entry['post_title']!r} → {suggested['filename']} "
                    f"({suggested['subject']}, sim {suggested['similarity']:.3f})"
                )
            else:
                misses += 1
                got = f"{suggested['filename']} ({suggested['subject']})" if suggested else "no_confident_match"
                print(f"MISS  {entry['post_title']!r} → got {got}, expected a {expected} image")

    total = hits + misses
    precision = hits / total if total else 0.0
    print()
    print(f"top-1 precision: {hits}/{total} = {precision:.3f}")
    rej_total = rej_pass + rej_fail
    if rej_total:
        print(f"rejection accuracy: {rej_pass}/{rej_total} = {rej_pass / rej_total:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
