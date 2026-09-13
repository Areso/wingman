#!/usr/bin/env python3
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from urllib.parse import urlparse

SOURCE_NAME = "Vimeo"
ALLOWED_HOSTS = {'player.vimeo.com', 'vimeo.com', 'www.vimeo.com'}

USAGE = f"""Usage:
  {Path(sys.argv[0]).name} <URL>
  {Path(sys.argv[0]).name} video <URL>
  {Path(sys.argv[0]).name} audio <URL>

A URL by itself defaults to video.
Files are saved under $WINGMAN_MEDIA_DIR/{SOURCE_NAME.lower()} when
WINGMAN_MEDIA_DIR is set, otherwise under ./downloads.
Only download media you own or are authorized to save.
"""


def _tokens(argv: list[str]) -> list[str]:
    if not argv:
        return []
    # Wingman passes user input as one positional "option" argument. Direct
    # shell execution may instead pass mode and URL as separate arguments.
    if len(argv) == 1:
        try:
            return shlex.split(argv[0])
        except ValueError as exc:
            raise ValueError(f"Invalid input: {exc}") from exc
    return argv


def parse_request(argv: list[str]) -> tuple[str, str]:
    parts = _tokens(argv)
    if len(parts) == 1:
        return "video", parts[0]
    if len(parts) == 2 and parts[0].lower() in {"video", "audio"}:
        return parts[0].lower(), parts[1]
    raise ValueError(USAGE.strip())


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http:// and https:// URLs are accepted.")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not accepted.")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in ALLOWED_HOSTS:
        allowed = ", ".join(sorted(ALLOWED_HOSTS))
        raise ValueError(f"This plugin only accepts {SOURCE_NAME} URLs. Allowed hosts: {allowed}")
    return url


def output_dir() -> Path:
    configured = os.environ.get("WINGMAN_MEDIA_DIR", "").strip()
    if configured:
        base = Path(configured).expanduser()
        return base / SOURCE_NAME.lower()
    return Path(__file__).resolve().parent / "downloads"


def download(mode: str, url: str) -> Path:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError(
            "yt-dlp is not installed. Run: python3 -m venv .venv && "
            ".venv/bin/pip install -r requirements.txt"
        ) from exc

    dest = output_dir()
    dest.mkdir(parents=True, exist_ok=True)
    finished: list[Path] = []

    def hook(data):
        if data.get("status") == "finished" and data.get("filename"):
            finished.append(Path(data["filename"]))

    if mode == "audio":
        fmt = "bestaudio[ext=m4a]/bestaudio"
    else:
        # Prefer a single-file MP4 to avoid requiring ffmpeg for merging.
        fmt = "best[ext=mp4]/best"

    opts = {
        "format": fmt,
        "paths": {"home": str(dest)},
        "outtmpl": "%(title).120B-%(id)s.%(ext)s",
        "noplaylist": True,
        "playlist_items": "1",
        "max_downloads": 1,
        "restrictfilenames": True,
        "windowsfilenames": True,
        "progress_hooks": [hook],
        "quiet": False,
        "no_warnings": False,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if finished:
            path = finished[-1]
        else:
            path = Path(ydl.prepare_filename(info))
            if not path.is_absolute():
                path = dest / path.name

    return path.resolve()


def main() -> int:
    try:
        mode, url = parse_request(sys.argv[1:])
        validate_url(url)
        path = download(mode, url)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1

    print(f"{SOURCE_NAME} {mode} downloaded successfully.")
    print(f"Saved to: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
