"""Convert browser-exported JSON cookies into Netscape format for yt-dlp."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _parse_cookie_sources(cookies_json: str) -> list[Path]:
    """Split comma/space-separated cookie JSON paths."""
    raw = cookies_json.replace(",", " ")
    return [Path(part.strip()).expanduser() for part in raw.split() if part.strip()]


def json_cookies_to_netscape(cookies: list[dict[str, Any]]) -> str:
    """Convert Chrome/Edge JSON cookie export to Netscape cookie file contents."""
    lines = [
        "# Netscape HTTP Cookie File",
        "# This file was generated from JSON cookies.",
    ]
    for cookie in cookies:
        domain = str(cookie.get("domain") or "")
        if not domain or not cookie.get("name"):
            continue
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        path = str(cookie.get("path") or "/")
        secure = "TRUE" if cookie.get("secure") else "FALSE"
        expiry = cookie.get("expirationDate")
        if cookie.get("session") or expiry is None:
            expires = "0"
        else:
            expires = str(int(float(expiry)))
        name = str(cookie["name"])
        value = str(cookie.get("value") or "")
        lines.append(
            "\t".join([domain, include_subdomains, path, secure, expires, name, value])
        )
    return "\n".join(lines) + "\n"


def ensure_netscape_cookie_file(cookies_json: str | Path) -> Path:
    """
    Read one or more JSON cookie files and write a merged Netscape cookie file.

    `cookies_json` may be a single path or comma/space-separated paths, e.g.:
    `./cookies/youtube.json,./cookies/tiktok.json`

    Returns the path to the Netscape cookie file for yt-dlp `cookiefile`.
    """
    sources = _parse_cookie_sources(str(cookies_json))
    if not sources:
        raise FileNotFoundError("No cookies JSON paths configured")

    resolved: list[Path] = []
    for source in sources:
        path = source.resolve()
        if not path.exists():
            raise FileNotFoundError(f"Cookies JSON not found: {path}")
        resolved.append(path)

    # Single file keeps sibling .txt; multiple files merge into cookies/combined.txt
    if len(resolved) == 1:
        target = resolved[0].with_suffix(".txt")
    else:
        target = resolved[0].parent / "combined.txt"

    newest_source_mtime = max(path.stat().st_mtime for path in resolved)
    if not target.exists() or newest_source_mtime > target.stat().st_mtime:
        merged: list[dict[str, Any]] = []
        for path in resolved:
            cookies = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(cookies, list):
                raise ValueError(f"Cookies JSON must be an array: {path}")
            merged.extend(cookies)
        target.write_text(json_cookies_to_netscape(merged), encoding="utf-8")
        target.chmod(0o600)

    return target
