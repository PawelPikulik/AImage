"""Download the image corpus described by data/corpus_manifest.json.

Reproducible by anyone: images come from Wikimedia Commons (freely licensed —
see `license` / `artist` / `source_url` per manifest entry). After downloading,
each file's SHA256 is verified against the manifest, so a corrupted or changed
image fails loudly instead of silently skewing evaluation results.

Usage:  python scripts/download_corpus.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

USER_AGENT = "flyrank-capstone-image-relevance/1.0 (educational capstone project)"
ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "data" / "corpus_manifest.json"
CORPUS_DIR = ROOT / "data" / "corpus"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp, dest.open("wb") as out:
        out.write(resp.read())


def main() -> int:
    entries = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0
    changed = False
    for entry in entries:
        dest = CORPUS_DIR / entry["filename"]
        if dest.exists():
            digest = sha256_of(dest)
            if entry.get("sha256") and digest != entry["sha256"]:
                print(f"FAIL {entry['filename']}: sha256 mismatch (delete the file and re-run)")
                failures += 1
            else:
                print(f"ok   {entry['filename']} (cached)")
            continue
        try:
            download(entry["download_url"], dest)
        except Exception as exc:  # noqa: BLE001 — report and continue with the rest
            print(f"FAIL {entry['filename']}: {exc}")
            dest.unlink(missing_ok=True)
            failures += 1
            continue
        digest = sha256_of(dest)
        if entry.get("sha256") and digest != entry["sha256"]:
            print(f"FAIL {entry['filename']}: downloaded sha256 does not match manifest")
            failures += 1
            continue
        if not entry.get("sha256"):
            entry["sha256"] = digest
            changed = True
        size_kb = dest.stat().st_size // 1024
        print(f"ok   {entry['filename']} ({size_kb} KB) [{entry['license']}]")
        time.sleep(0.3)  # be polite to the CDN
    if changed:
        MANIFEST_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("Manifest updated with sha256 checksums.")
    print(f"Done: {len(entries) - failures}/{len(entries)} images ready in {CORPUS_DIR}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
