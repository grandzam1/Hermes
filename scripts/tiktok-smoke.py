#!/usr/bin/env python3
"""Smoke-test TikTok extract/download via Hermes yt-dlp settings (for CI or local)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1] / "packages" / "hermes-api"
sys.path.insert(0, str(API_DIR))

from app.core.config import settings  # noqa: E402
from app.services.yt_dlp_service import YTDLPService  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description="TikTok yt-dlp smoke test")
    parser.add_argument(
        "url",
        nargs="?",
        default=os.environ.get(
            "TIKTOK_TEST_URL",
            "https://www.tiktok.com/@/video/7650809680276507924",
        ),
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download the video to a temp file (default: metadata only)",
    )
    args = parser.parse_args()

    print("=== TikTok smoke test ===")
    print(f"url={args.url}")
    print(f"cookies_configured={bool(settings.cookies_json)}")
    print(
        f"impersonate={settings.ytdlp_impersonate!r} sites={settings.ytdlp_impersonate_sites!r}"
    )
    print(f"user_agent={settings.ytdlp_user_agent!r}")

    if not settings.cookies_json:
        print("ERROR: HERMES_COOKIES_JSON is not set (add TikTok cookies for CI)")
        return 1

    service = YTDLPService()
    opts = service._build_opts(args.url)
    print(
        "opts:",
        {
            "has_cookiefile": bool(opts.get("cookiefile")),
            "has_impersonate": bool(opts.get("impersonate")),
            "user_agent": (opts.get("http_headers") or {}).get("User-Agent"),
        },
    )

    info = await service.extract_info(args.url, download=False, extract_flat=False)
    if not info:
        print("FAIL: could not extract TikTok metadata")
        return 1

    print(f"OK metadata: id={info.get('id')} title={info.get('title')!r}")

    if args.download:
        out_dir = API_DIR / "temp" / "tiktok-smoke"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_tpl = str(out_dir / "%(id)s.%(ext)s")
        path = await service.download_video(
            args.url,
            output_path=out_tpl,
            format_spec="best",
        )
        if not path or not Path(path).exists():
            print("FAIL: download returned no file")
            return 1
        size = Path(path).stat().st_size
        print(f"OK download: {path} ({size} bytes)")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
