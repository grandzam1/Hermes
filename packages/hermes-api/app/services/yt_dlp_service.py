"""
YoutubeDL service wrapper for async operations.
"""

import asyncio
import os
import shutil
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import DownloadError, ExtractorError, YoutubeDLError

from app.core.config import settings
from app.core.logging import get_logger
from app.utils.cookies import ensure_netscape_cookie_file
from app.utils.media import VIDEO_EXTENSIONS

logger = get_logger(__name__)

_IMPERSONATE_AVAILABLE: Optional[bool] = None


def _resolve_impersonate_target(raw: str) -> Optional[ImpersonateTarget]:
    """Parse configured target into an ImpersonateTarget (nightly requires this type)."""
    value = (raw or "").strip()
    if not value or value.lower() in {"off", "false", "0", "none"}:
        return None
    # Accept "chrome" or more specific "chrome-131:windows-10"
    try:
        return ImpersonateTarget.from_str(value) if hasattr(ImpersonateTarget, "from_str") else ImpersonateTarget(value)
    except Exception:
        return ImpersonateTarget(value)


def _impersonate_supported(target: ImpersonateTarget | str) -> bool:
    """Return True if curl_cffi + yt-dlp can use the given impersonate target."""
    global _IMPERSONATE_AVAILABLE
    if _IMPERSONATE_AVAILABLE is False:
        return False
    resolved = (
        target
        if isinstance(target, ImpersonateTarget)
        else _resolve_impersonate_target(str(target))
    )
    if resolved is None:
        return False
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "impersonate": resolved}):
            _IMPERSONATE_AVAILABLE = True
            return True
    except (YoutubeDLError, AssertionError, Exception) as e:
        logger.warning(
            "yt-dlp impersonate unavailable; continuing without it",
            target=str(resolved),
            error=str(e) or type(e).__name__,
        )
        _IMPERSONATE_AVAILABLE = False
        return False


def _site_match(url: str, sites_raw: str) -> bool:
    """Return True if URL host matches configured site fragments."""
    sites = (sites_raw or "").strip().lower()
    if not sites:
        return False
    if sites in {"all", "*"}:
        return True
    host = (urlparse(url).hostname or "").lower()
    fragments = [part.strip() for part in sites.replace(",", " ").split() if part.strip()]
    return any(fragment in host for fragment in fragments)


def _should_impersonate(url: str) -> bool:
    """Decide whether impersonation applies for this URL (default: TikTok only)."""
    raw_target = (settings.ytdlp_impersonate or "").strip()
    if not raw_target or raw_target.lower() in {"off", "false", "0", "none"}:
        return False
    return _site_match(url, settings.ytdlp_impersonate_sites or "tiktok")


def _should_override_user_agent(url: str) -> bool:
    """Apply the short UA override only on selected sites (TikTok by default)."""
    raw_ua = (settings.ytdlp_user_agent or "").strip()
    if not raw_ua or raw_ua.lower() in {"off", "false", "0", "none"}:
        return False
    # Reuse the same site scope as impersonation so YouTube stays untouched.
    return _site_match(url, settings.ytdlp_impersonate_sites or "tiktok")


class YTDLPService:
    """Async wrapper for yt-dlp operations."""

    def __init__(self):
        self._default_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }
        if shutil.which("node"):
            self._default_opts["js_runtimes"] = {"node": {}}

    def _build_opts(self, url: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        opts = self._default_opts.copy()
        # Prefer explicit per-request cookie options over the shared cookies JSON.
        if "cookiefile" not in kwargs and "cookiesfrombrowser" not in kwargs:
            cookies_json = settings.cookies_json
            if cookies_json:
                try:
                    opts["cookiefile"] = str(ensure_netscape_cookie_file(cookies_json))
                except Exception as e:
                    logger.warning(
                        "Failed to load cookies JSON; continuing without cookies",
                        path=cookies_json,
                        error=str(e),
                    )

        # Impersonation is opt-in via settings and scoped by site (TikTok-only default)
        # so already-working extractors (e.g. YouTube) stay unchanged.
        if (
            url
            and "impersonate" not in kwargs
            and _should_impersonate(url)
        ):
            target = _resolve_impersonate_target(settings.ytdlp_impersonate or "")
            if target is not None and _impersonate_supported(target):
                opts["impersonate"] = target

        # TikTok on some datacenter IPs needs a short UA alongside impersonate+cookies.
        if (
            url
            and "http_headers" not in kwargs
            and _should_override_user_agent(url)
        ):
            ua = (settings.ytdlp_user_agent or "").strip()
            if ua:
                headers = dict(opts.get("http_headers") or {})
                headers["User-Agent"] = ua
                opts["http_headers"] = headers

        opts.update(kwargs)
        return opts

    async def extract_info(
        self, url: str, download: bool = False, **kwargs
    ) -> Optional[Dict[str, Any]]:
        """
        Extract video information asynchronously.

        Args:
            url: Video URL to extract information from
            download: Whether to download the video
            **kwargs: Additional yt-dlp options

        Returns:
            Video information dictionary or None if extraction fails
        """

        def _extract_info_sync():
            try:
                opts = self._build_opts(url, **kwargs)

                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(url, download=download)

            except (DownloadError, ExtractorError) as e:
                logger.warning("Failed to extract info from URL", url=url, error=str(e))
                return None
            except Exception as e:
                logger.error(
                    "Unexpected error during info extraction", url=url, error=str(e)
                )
                return None

        # Run in thread pool to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _extract_info_sync)

    async def download_video(
        self,
        url: str,
        output_path: str,
        format_spec: str = "best",
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        **kwargs,
    ) -> Optional[str]:
        """
        Download video asynchronously.

        Args:
            url: Video URL to download
            output_path: Path where to save the video
            format_spec: Format selection specification
            progress_callback: Optional callback for progress updates
            **kwargs: Additional yt-dlp options

        Returns:
            Path to downloaded file or None if download fails
        """

        def _download_sync():
            try:
                opts = self._build_opts(
                    url,
                    format=format_spec,
                    outtmpl=output_path,
                    restrictfilenames=True,  # Ensure safe filenames
                    **kwargs,
                )

                # Add progress hook if callback provided
                if progress_callback:
                    opts["progress_hooks"] = [progress_callback]

                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if info:
                        # Get the actual filename from yt-dlp
                        actual_path = ydl.prepare_filename(info)
                        logger.info("Prepared filename from yt-dlp", path=actual_path)

                        # Check if the file exists and has a proper extension
                        if actual_path and os.path.exists(actual_path):
                            # File exists with proper extension, return it
                            return actual_path
                        else:
                            # Template didn't work, try to find the actual file
                            directory = os.path.dirname(actual_path)
                            base_name = os.path.basename(actual_path)

                            # Remove the template part if it exists
                            if ".%(ext)s" in base_name:
                                base_name = base_name.replace(".%(ext)s", "")

                            # Look for the actual downloaded file with common extensions
                            for ext in VIDEO_EXTENSIONS:
                                potential_path = os.path.join(
                                    directory, base_name + ext
                                )
                                if os.path.exists(potential_path):
                                    return potential_path

                            # If still not found, get extension from format info
                            formats = info.get("formats", [])
                            if formats:
                                # Find the format that matches our format_spec
                                selected_format = None
                                for fmt in formats:
                                    if fmt.get("format_id") == format_spec:
                                        selected_format = fmt
                                        break
                                    elif format_spec in ["best", "worst"] and fmt.get(
                                        "format_note"
                                    ):
                                        if (
                                            "best" in format_spec.lower()
                                            and "best"
                                            in fmt.get("format_note", "").lower()
                                        ):
                                            selected_format = fmt
                                            break

                                # Fallback to first format with extension
                                if not selected_format:
                                    for fmt in formats:
                                        if fmt.get("ext"):
                                            selected_format = fmt
                                            break

                                if selected_format and selected_format.get("ext"):
                                    ext = selected_format.get("ext")
                                    correct_path = os.path.join(
                                        directory, base_name + f".{ext}"
                                    )
                                    if os.path.exists(correct_path):
                                        return correct_path

                        # If we can't find the file, return the prepared path anyway
                        # (it might have been created with a different extension)
                        return actual_path

                return None

            except (DownloadError, ExtractorError) as e:
                logger.warning("Failed to download video", url=url, error=str(e))
                return None
            except Exception as e:
                logger.error("Unexpected error during download", url=url, error=str(e))
                return None

        # Run in thread pool to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _download_sync)

    def get_supported_extractors(self) -> list[str]:
        """Get list of supported extractor names."""
        return list(
            yt_dlp.extractor.get_info_extractor.__wrapped__.__defaults__[0].keys()
        )

    def validate_url(self, url: str) -> bool:
        """
        Validate if URL can be handled by yt-dlp.

        Args:
            url: URL to validate

        Returns:
            True if URL is supported, False otherwise
        """
        try:
            # Try to find a suitable extractor
            from yt_dlp.extractor import gen_extractor_classes

            for ie_class in gen_extractor_classes():
                ie = ie_class()
                if ie.suitable(url):
                    return True

            return False

        except Exception:
            return False
