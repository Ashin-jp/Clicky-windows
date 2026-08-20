"""
executors/web_actions.py — Web & API Actions

Handles: FETCH_URL, DOWNLOAD, SUMMARISE_PAGE
Uses httpx for HTTP requests and BeautifulSoup for HTML extraction.
"""

import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from executors import register_action, ActionResult
from storage import WORKSPACE_DIR

logger = logging.getLogger(__name__)

MAX_FETCH_CHARS = 15_000  # Max chars to inject as AI context
DOWNLOAD_DIR = Path.home() / "Downloads"
REQUEST_TIMEOUT = 15.0


def _extract_text_from_html(html: str) -> str:
    """Extract readable text from HTML. Uses BeautifulSoup if available, falls back to regex."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # Remove script and style elements
        for element in soup(["script", "style", "nav", "footer", "header"]):
            element.decompose()

        text = soup.get_text(separator="\n", strip=True)
    except ImportError:
        # Fallback: basic regex HTML stripping
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

    # Clean up excessive whitespace
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n".join(lines)


@register_action(
    "FETCH_URL", "🌐 Fetch URL", "Get content from a URL", "web"
)
def handle_fetch_url(params: str) -> ActionResult:
    """
    Fetch a URL and extract text content.
    Params: "url"
    """
    url = params.strip().strip('"').strip("'")
    if not url:
        return ActionResult(False, "No URL specified")

    # Auto-add https://
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Clicky/1.0",
            })
            response.raise_for_status()

        content_type = response.headers.get("content-type", "")

        if "html" in content_type:
            text = _extract_text_from_html(response.text)
        elif "json" in content_type:
            text = response.text
        elif "text" in content_type:
            text = response.text
        else:
            return ActionResult(
                False,
                f"URL returned non-text content ({content_type}). Use DOWNLOAD instead.",
            )

        # Truncate
        if len(text) > MAX_FETCH_CHARS:
            text = text[:MAX_FETCH_CHARS] + f"\n\n... [truncated, page has {len(text)} chars total]"

        domain = urlparse(url).netloc
        logger.info(f"Action: fetched {domain} ({len(text)} chars)")

        return ActionResult(
            success=True,
            message=f"Fetched {domain}",
            data=text,
            inject_context=True,
            context_label=f"Content from {url}",
        )
    except httpx.HTTPStatusError as e:
        return ActionResult(False, f"HTTP {e.response.status_code}: {url}")
    except httpx.ConnectError:
        return ActionResult(False, f"Could not connect to {url}")
    except httpx.TimeoutException:
        return ActionResult(False, f"Request timed out: {url}")
    except Exception as e:
        return ActionResult(False, f"Fetch failed: {e}")


@register_action(
    "DOWNLOAD", "⬇️ Download", "Download a file from URL", "web"
)
def handle_download(params: str) -> ActionResult:
    """
    Download a file from URL. Params: "url" or "url|save_path"
    Default save location is ~/Downloads/
    """
    if "|" in params:
        url, save_path = params.split("|", 1)
        url = url.strip()
        save_path = Path(save_path.strip())
    else:
        url = params.strip().strip('"').strip("'")
        # Extract filename from URL
        parsed = urlparse(url)
        filename = Path(parsed.path).name or "download"
        save_path = DOWNLOAD_DIR / filename

    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            response = client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Clicky/1.0",
            })
            response.raise_for_status()

        save_path = Path(save_path).resolve()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(response.content)

        size_kb = len(response.content) // 1024
        logger.info(f"Action: downloaded {url} → {save_path} ({size_kb}KB)")

        return ActionResult(
            success=True,
            message=f"Downloaded to {save_path.name} ({size_kb}KB)",
        )
    except Exception as e:
        return ActionResult(False, f"Download failed: {e}")


@register_action(
    "SUMMARISE_PAGE", "📄 Summarise Page",
    "Fetch and summarise a webpage", "web"
)
def handle_summarise_page(params: str) -> ActionResult:
    """
    Fetch a URL and inject it for AI summarisation.
    This is essentially FETCH_URL but with a specific context label
    that tells the AI to summarise.
    Params: "url"
    """
    result = handle_fetch_url(params)
    if not result.success:
        return result

    # Override the context label to instruct summarisation
    result.context_label = f"[SUMMARISE THIS] Content from {params.strip()}"
    return result
