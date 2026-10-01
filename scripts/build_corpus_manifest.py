"""Build data/corpus_manifest.json from the Wikimedia Commons API.

The corpus uses freely licensed images (Wikimedia Commons, CC / public domain —
each entry records the license short name, artist, and source page URL). The
brief suggests Unsplash/Pexels; Commons was chosen because it offers a keyless,
scriptable API so the corpus is reproducible by anyone with one command.

This script is a maintainer tool: it re-resolves the curated title list to
fresh thumb URLs and license metadata, and rewrites the manifest. Normal users
do not need it — `scripts/download_corpus.py` consumes the committed manifest.

Usage:  python scripts/build_corpus_manifest.py
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "flyrank-capstone-image-relevance/1.0 (educational capstone project)"
MANIFEST_PATH = Path(__file__).resolve().parent.parent / "data" / "corpus_manifest.json"

# Curated Commons file titles per corpus category. Chosen so every image is a
# clear, mostly uncluttered photograph of the subject — except the two
# "ambiguous" entries (an artwork and a taxidermy mount), which exist to
# exercise the low-confidence flagging path (PROBE 1).
CURATED: dict[str, list[str]] = {
    "red_fox": [
        "File:Alaska Red Fox (Vulpes vulpes).jpg",
        "File:Arabian Red Fox with Juvenile.jpg",
        "File:Common foxes in the snow.jpg",
        "File:Mlade lisice u rupi.jpg",
        "File:Portrait of a red fox in Rautas fjällurskog.jpg",
        "File:Red Fox (Vulpes vulpes) (4).jpg",
        "File:Red Fox drinking from a lake in Gennevilliers, France.jpg",
        "File:Red fox (Vulpes vulpes crucigera) Skalnate Pleso 2.jpg",
        "File:Red fox catches a vole - Tolyatti, Russia.jpg",
        "File:Red fox kits playing Bombay Hook (36455).jpg",
        "File:Red Fox 2025 10 07.jpg",
        "File:Vulpes vulpes laying in snow.jpg",
    ],
    "wolf": [
        "File:Canis lupus - Wildpark Knüll 03.jpg",
        "File:Canis lupus Ernstbrunn.jpg",
        "File:Canis lupus Kopf.JPG",
        "File:Canis lupus PO.jpg",
        "File:Canis lupus baileyi running.jpg",
        "File:Canis lupus lupus in Alpenzoo, Innsbruck.jpg",
        "File:Canis lupus signatus - 01.jpg",
        "File:Endangered gray wolf (Canis lupus).jpg",
    ],
    "dog": [
        "File:Cane corso głowa profil 493o.jpg",
        "File:DSC09611a Search and Rescue Dog, Austrian Red Cross Perchtoldsdorf, 2024-10.jpg",
        "File:Domestic dog in Ericeira, Portugal 003.jpg",
        "File:Domestic dog in Ericeira, Portugal 019.jpg",
        "File:Domestic dog in Ericeira, Portugal 042.jpg",
        "File:Domestic dog in Ericeira, Portugal 053.jpg",
        "File:Domestic dog in Ericeira, Portugal 082.jpg",
        "File:Domestic dog in Ericeira, Portugal 116.jpg",
    ],
    "bear": [
        "File:Brown bear (Ursus arctos arctos) running.jpg",
        "File:Eurasian brown bear (Ursus arctos arctos) adult female head.jpg",
        "File:Eurasian brown bear (Ursus arctos arctos) cub 14 months.jpg",
        "File:Eurasian brown bear (Ursus arctos arctos) cub 14 months scratching back.jpg",
        "File:Eurasian brown bear (Ursus arctos arctos) female 1.jpg",
        "File:Eurasian brown bear (Ursus arctos arctos) female 2.jpg",
        "File:Eurasian brown bear (Ursus arctos arctos) female 3.jpg",
        "File:Eurasian brown bear (Ursus arctos arctos) female 4.jpg",
    ],
    "deer": [
        "File:Chital in Sanjay Dubri Tiger Reserve December 2024 by Tisha Mukherjee 01.jpg",
        "File:Chital in Sundarbans National Park July 2025 by Tisha Mukherjee 02.jpg",
        "File:Fallow Deer (Dama dama).jpg",
        "File:Fallow Deer in the German wood.jpg",
        "File:Fallow deer in field.jpg",
        "File:RedDeerStag.jpg",
        "File:Red deer (Cervus elaphus) hind.jpg",
        "File:Red deer (Cervus elaphus) young stag.jpg",
    ],
    # Artwork + taxidermy: expect the vision model to be less certain here.
    "ambiguous": [
        "File:Americana Fox - Red Fox.jpg",
        "File:Taxidermy of Red fox - 1.jpg",
    ],
}

PREFIX = {"red_fox": "fox", "wolf": "wolf", "dog": "dog", "bear": "bear", "deer": "deer", "ambiguous": "ambiguous"}


def _get(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_imageinfo(titles: list[str]) -> dict[str, dict]:
    """Return {title: imageinfo} for the given Commons titles (batched)."""
    result: dict[str, dict] = {}
    for i in range(0, len(titles), 25):  # API limit: 50 titles; stay safe
        batch = titles[i : i + 25]
        data = _get(
            {
                "action": "query",
                "format": "json",
                "titles": "|".join(batch),
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "iiurlwidth": "640",
            }
        )
        for page in (data.get("query", {}).get("pages", {}) or {}).values():
            if "missing" in page:
                print(f"  WARNING: missing on Commons: {page.get('title')}", file=sys.stderr)
                continue
            info = (page.get("imageinfo") or [{}])[0]
            if info.get("mime") != "image/jpeg":
                print(f"  WARNING: not JPEG, skipped: {page['title']} ({info.get('mime')})", file=sys.stderr)
                continue
            if (info.get("width") or 0) < 400:
                print(f"  WARNING: too small, skipped: {page['title']}", file=sys.stderr)
                continue
            result[page["title"]] = info
        time.sleep(0.5)  # be polite to the API
    return result


def _strip_html(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def main() -> int:
    entries: list[dict] = []
    for category, titles in CURATED.items():
        print(f"[{category}] resolving {len(titles)} titles…")
        infos = fetch_imageinfo(titles)
        missing = [t for t in titles if t not in infos]
        for t in missing:
            print(f"  ERROR: no usable imageinfo for {t}", file=sys.stderr)
        for n, title in enumerate(titles, start=1):
            info = infos.get(title)
            if not info:
                continue
            meta = info.get("extmetadata", {}) or {}
            entries.append(
                {
                    "filename": f"{PREFIX[category]}-{n:02d}.jpg",
                    "category": category,
                    "source_title": title,
                    "source_url": info.get("descriptionurl"),
                    "download_url": info.get("thumburl") or info.get("url"),
                    "license": _strip_html(meta.get("LicenseShortName", {}).get("value", "")),
                    "artist": _strip_html(meta.get("Artist", {}).get("value", "")),
                    "sha256": None,  # filled in by scripts/download_corpus.py
                }
            )
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(entries)} entries to {MANIFEST_PATH}")
    return 0 if len(entries) == sum(len(v) for v in CURATED.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
