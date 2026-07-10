#!/usr/bin/env python3
"""
YouTube Script Extractor
========================

Pulls the spoken script (transcript) out of YouTube videos — including
members-only videos you have paid access to — and turns messy auto-captions
into a clean, readable script.

How access works
----------------
This tool does NOT bypass YouTube's paywall. For members-only videos it rides
on YOUR existing login: it reads the cookies from the browser where you're
already signed in as a paying member (via yt-dlp's --cookies-from-browser).
If your account can watch the video, this can read its captions; if it can't,
neither can this. Use it on your own memberships / your own content.

Pipeline
--------
  URL --> yt-dlp downloads caption track (auto or manual) as .vtt
      --> parse + dedupe rolling captions into raw text
      --> (optional) Claude cleans it into a punctuated, paragraphed script
      --> write .txt / .md

Usage
-----
  # Public video
  python yt_script_extractor.py "https://youtu.be/VIDEO_ID"

  # Members-only video (use the browser you're logged into as a member)
  python yt_script_extractor.py "URL" --browser chrome

  # If Chrome's cookie DB is locked/encrypted, export cookies.txt instead
  # (browser extension: "Get cookies.txt LOCALLY") and use:
  python yt_script_extractor.py "URL" --cookies-file cookies.txt

  # Several at once, cleaned into a readable script with Claude
  python yt_script_extractor.py URL1 URL2 --browser chrome --ai-clean

  # Pick caption language / output folder / format
  python yt_script_extractor.py "URL" --lang en --out ./scripts --format md

Requirements
------------
  pip install yt-dlp
  pip install anthropic          # only if you use --ai-clean
  export ANTHROPIC_API_KEY=...   # only if you use --ai-clean
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# --------------------------------------------------------------------------- #
# 1. Fetch caption files with yt-dlp                                           #
# --------------------------------------------------------------------------- #
def get_video_meta(url: str, auth_args: list) -> dict:
    """Return {'id', 'title', 'channel'} for a URL using yt-dlp's -J dump."""
    import json

    cmd = ["yt-dlp", "--skip-download", "--dump-single-json", "--no-warnings"]
    cmd += auth_args
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ExtractionError(_friendly_error(proc.stderr, url))
    data = json.loads(proc.stdout)
    return {
        "id": data.get("id", "video"),
        "title": data.get("title", "Untitled"),
        "channel": data.get("channel") or data.get("uploader", ""),
    }


def download_captions(url: str, lang: str, auth_args: list, workdir: Path) -> Path:
    """
    Download the best available caption track for `url` into `workdir`.
    Prefers manual subs, falls back to auto-generated. Returns the .vtt path.
    """
    out_tmpl = str(workdir / "%(id)s.%(ext)s")
    # Try manual subs first, then auto subs. `--sub-langs "lang.*"` also grabs
    # regional variants like en-US, en-GB.
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", f"{lang}.*,{lang}",
        "--sub-format", "vtt",
        "--no-warnings",
        "-o", out_tmpl,
    ]
    cmd += auth_args
    cmd.append(url)

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ExtractionError(_friendly_error(proc.stderr, url))

    vtts = sorted(workdir.glob("*.vtt"))
    if not vtts:
        raise ExtractionError(
            f"No captions found for this video in language '{lang}'.\n"
            f"  - The creator may not have added subtitles and auto-captions "
            f"may be off.\n"
            f"  - Try a different --lang, or check the video has captions on "
            f"YouTube (the 'CC' button)."
        )
    # Prefer a manual track (filename has no 'auto') if present.
    manual = [p for p in vtts if "auto" not in p.name.lower()]
    return (manual or vtts)[0]


def _friendly_error(stderr: str, url: str) -> str:
    s = stderr.lower()
    if "could not copy" in s and "cookie database" in s:
        return (
            "Chrome's cookie database is locked or encrypted.\n"
            "  Fix options (in order of ease):\n"
            "  1. Fully close Chrome first:  taskkill /F /IM chrome.exe /T\n"
            "     then re-run this command.\n"
            "  2. Use Firefox instead: log into YouTube in Firefox, then run "
            "with --browser firefox\n"
            "  3. Export cookies.txt with the 'Get cookies.txt LOCALLY' "
            "extension and run with --cookies-file cookies.txt"
        )
    if "members-only" in s or "join this channel" in s or "members only" in s:
        return (
            f"This looks like a members-only video and the current login can't "
            f"see it.\n"
            f"  - Pass --browser <chrome|firefox|edge|brave|safari> for the "
            f"browser where you're signed in as a member of that channel.\n"
            f"  - Make sure that membership is active."
        )
    if "sign in" in s or "cookies" in s or "age" in s and "confirm" in s:
        return (
            f"YouTube wants a signed-in session for this video.\n"
            f"  - Add --browser <chrome|firefox|edge|brave|safari> so yt-dlp can "
            f"use your login cookies."
        )
    if "private video" in s:
        return "This video is private — no account can extract it unless invited."
    if "unavailable" in s or "does not exist" in s:
        return f"Video unavailable or the URL is wrong:\n  {url}"
    # Fall back to the raw yt-dlp error (trimmed).
    lines = [ln for ln in stderr.splitlines() if ln.strip()]
    tail = "\n  ".join(lines[-4:]) if lines else "unknown error"
    return f"yt-dlp failed:\n  {tail}"


# --------------------------------------------------------------------------- #
# 2. Parse VTT -> clean raw text                                              #
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")            # <00:00:01.000>, <c>, </c>
_TS_RE = re.compile(r"^\d{2}:\d{2}:\d{2}")  # cue timing lines
_CUE_NUM_RE = re.compile(r"^\d+$")


def parse_vtt(vtt_path: Path) -> str:
    """
    Turn a WEBVTT caption file into clean prose text.

    YouTube auto-captions use a "rolling" format where each cue repeats the
    tail of the previous cue plus one new line, so naive concatenation
    triples every line. We keep insertion order and drop any line already
    emitted immediately before, plus lines fully contained in the previous one.
    """
    raw = vtt_path.read_text(encoding="utf-8", errors="ignore")
    out: list[str] = []

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line == "WEBVTT" or line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        if "-->" in line or _TS_RE.match(line) or _CUE_NUM_RE.match(line):
            continue

        line = _TAG_RE.sub("", line).strip()
        line = re.sub(r"\s+", " ", line)
        if not line:
            continue

        # Skip exact repeats and lines already covered by the previous line.
        if out:
            prev = out[-1]
            if line == prev:
                continue
            if line in prev:
                continue
            # Rolling overlap: previous line's tail == this line's head.
            if prev.endswith(line):
                continue
        out.append(line)

    text = " ".join(out)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# --------------------------------------------------------------------------- #
# 3. Optional: clean into a readable script with Claude                        #
# --------------------------------------------------------------------------- #
CLEANUP_SYSTEM = (
    "You are a transcript editor. You are given the raw, auto-generated "
    "spoken text of a video with no punctuation or paragraphs. Restore natural "
    "punctuation, capitalization, and paragraph breaks. Fix obvious "
    "speech-to-text errors ONLY when unambiguous. Do NOT summarize, add, "
    "remove, reorder, or reword content — preserve every spoken idea verbatim. "
    "Return only the cleaned script, nothing else."
)


def ai_clean(text: str, model: str) -> str:
    """Punctuate + paragraph the raw transcript via the Claude API."""
    try:
        import anthropic
    except ImportError:
        raise ExtractionError(
            "--ai-clean needs the anthropic package:  pip install anthropic"
        )
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ExtractionError("--ai-clean needs env var ANTHROPIC_API_KEY.")

    client = anthropic.Anthropic()
    chunks = _chunk(text, max_chars=12000)
    cleaned: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        if len(chunks) > 1:
            print(f"      cleaning chunk {i}/{len(chunks)}...", file=sys.stderr)
        msg = client.messages.create(
            model=model,
            max_tokens=8000,
            system=CLEANUP_SYSTEM,
            messages=[{"role": "user", "content": chunk}],
        )
        cleaned.append("".join(b.text for b in msg.content if b.type == "text"))
    return "\n\n".join(cleaned).strip()


def _chunk(text: str, max_chars: int) -> list[str]:
    """Split long text on sentence-ish boundaries so each piece fits the model."""
    if len(text) <= max_chars:
        return [text]
    words = text.split(" ")
    chunks, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > max_chars:
            chunks.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        chunks.append(cur)
    return chunks


# --------------------------------------------------------------------------- #
# 4. Orchestration                                                            #
# --------------------------------------------------------------------------- #
class ExtractionError(Exception):
    pass


def _safe_name(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s).strip()
    return re.sub(r"[\s]+", "_", s)[:80] or "script"


def _auth_args(args) -> list:
    """Build yt-dlp auth flags: cookies file wins over browser cookies."""
    if args.cookies_file:
        cf = Path(args.cookies_file)
        if not cf.exists():
            raise ExtractionError(f"Cookies file not found: {cf}")
        return ["--cookies", str(cf)]
    if args.browser:
        return ["--cookies-from-browser", args.browser]
    return []


def _video_id_from_url(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else "video"


def extract_one(url: str, args) -> Path:
    auth = _auth_args(args)
    # Metadata is a nice-to-have (title/channel). If YouTube blocks the JSON
    # dump — e.g. the JS-challenge issue leaves "only images available" — don't
    # abort; the caption download uses --skip-download and may still succeed.
    try:
        meta = get_video_meta(url, auth)
    except ExtractionError:
        meta = {"id": _video_id_from_url(url), "title": "Untitled", "channel": ""}
    print(f"  • {meta['title']}  [{meta['channel']}]", file=sys.stderr)

    with tempfile.TemporaryDirectory() as td:
        vtt = download_captions(url, args.lang, auth, Path(td))
        text = parse_vtt(vtt)

    if not text:
        raise ExtractionError("Captions downloaded but produced empty text.")

    if args.ai_clean:
        print("      polishing with Claude...", file=sys.stderr)
        text = ai_clean(text, args.model)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = "md" if args.format == "md" else "txt"
    fname = f"{_safe_name(meta['title'])}_{meta['id']}.{ext}"
    path = out_dir / fname

    if args.format == "md":
        body = (
            f"# {meta['title']}\n\n"
            f"*Channel: {meta['channel']}*  \n"
            f"*Source: {url}*\n\n---\n\n{text}\n"
        )
    else:
        body = text + "\n"
    path.write_text(body, encoding="utf-8")
    return path


def main() -> int:
    p = argparse.ArgumentParser(
        description="Extract spoken scripts from YouTube videos (incl. your "
        "members-only videos) using your own login.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("urls", nargs="+", help="One or more YouTube video URLs")
    p.add_argument(
        "--browser",
        help="Browser to read login cookies from for members-only / private "
        "videos: chrome | firefox | edge | brave | safari | chromium",
    )
    p.add_argument(
        "--cookies-file",
        help="Path to a Netscape cookies.txt file (exported with a browser "
        "extension like 'Get cookies.txt LOCALLY'). Overrides --browser. "
        "Use this if --browser chrome fails with a cookie database error.",
    )
    p.add_argument("--lang", default="en", help="Caption language code (default: en)")
    p.add_argument(
        "--ai-clean",
        action="store_true",
        help="Use Claude to add punctuation/paragraphs (needs ANTHROPIC_API_KEY)",
    )
    p.add_argument(
        "--model",
        default="claude-haiku-4-5",
        help="Claude model for --ai-clean (default: claude-haiku-4-5)",
    )
    p.add_argument("--out", default="./scripts", help="Output folder (default: ./scripts)")
    p.add_argument(
        "--format", choices=["txt", "md"], default="txt", help="Output format"
    )
    args = p.parse_args()

    if not shutil.which("yt-dlp"):
        print("ERROR: yt-dlp not found. Install it with:  pip install yt-dlp",
              file=sys.stderr)
        return 2

    print(f"Extracting {len(args.urls)} video(s)...", file=sys.stderr)
    ok, failed = 0, 0
    for url in args.urls:
        try:
            path = extract_one(url, args)
            print(f"  ✓ saved -> {path}", file=sys.stderr)
            ok += 1
        except ExtractionError as e:
            print(f"  ✗ {url}\n    {e}", file=sys.stderr)
            failed += 1
        except Exception as e:  # unexpected
            print(f"  ✗ {url}\n    Unexpected error: {e}", file=sys.stderr)
            failed += 1

    print(f"\nDone. {ok} succeeded, {failed} failed.", file=sys.stderr)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())