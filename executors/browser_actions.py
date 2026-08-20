"""
executors/browser_actions.py — Browser automation action handlers.

Registers: BROWSER_SEARCH, BROWSER_NAVIGATE, BROWSER_CLICK, BROWSER_TYPE,
BROWSER_READ, BROWSER_SCROLL, BROWSER_TAB, BROWSER_BACK, BROWSER_FORWARD,
BROWSER_SCREENSHOT, BROWSER_FILL_FORM

All actions route through browser_controller.py's dedicated Playwright instance.

Fix 1 applied: Every handler calls the coroutine fresh at the point of
execution inside _run_async, never storing or reusing coroutine objects.
"""

import asyncio
import logging
import traceback

from executors import register_action, ActionResult

logger = logging.getLogger(__name__)


def _run_async(async_callable, *args, **kwargs):
    """
    Safely run an async method from sync context.

    CRITICAL: This takes a *callable* and its arguments, NOT a pre-built
    coroutine object. The coroutine is created fresh inside the execution
    context to prevent the "coroutine was never awaited" / double-await bug.

    Usage: _run_async(ctrl.search, "python tutorials")
    NOT:   _run_async(ctrl.search("python tutorials"))  # BAD — creates coro too early
    """
    import concurrent.futures

    def _run_in_thread():
        """Create a fresh event loop in a new thread and run the coroutine there."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Create the coroutine FRESH here, inside the execution thread
            coro = async_callable(*args, **kwargs)
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_in_thread)
            return future.result(timeout=45)
    except concurrent.futures.TimeoutError:
        logger.error(f"Browser action timed out: {async_callable.__name__}")
        return {"success": False, "message": "Browser action timed out after 45 seconds."}
    except Exception as e:
        logger.error(f"Browser action failed: {async_callable.__name__}: {e}\n{traceback.format_exc()}")
        return {"success": False, "message": f"Browser action failed: {e}"}


def _get_controller():
    from browser_controller import get_browser_controller
    return get_browser_controller()


@register_action("BROWSER_SEARCH", "🔍 Web Search", "Search the web using browser", "browser")
def handle_browser_search(params: str) -> ActionResult:
    """Open browser and search for a query (default Google)."""
    ctrl = _get_controller()
    result = _run_async(ctrl.search, params)
    return ActionResult(success=result["success"], message=result["message"])


@register_action("SITE_SEARCH", "🔎 Site Search", "Search a specific site", "browser")
def handle_site_search(params: str) -> ActionResult:
    """Search a specific site. Params format: domain|query or just query to use current site."""
    ctrl = _get_controller()
    parts = params.split("|", 1)
    if len(parts) == 2:
        domain, query = parts
        domain = domain.strip()
        query = query.strip()
        if domain.lower() == "current":
            domain = None
    else:
        domain = None
        query = params.strip()

    result = _run_async(ctrl.search_on_site, query, domain)
    return ActionResult(success=result["success"], message=result["message"])


@register_action("BROWSER_NAVIGATE", "🌐 Navigate", "Navigate to a URL", "browser")
def handle_browser_navigate(params: str) -> ActionResult:
    """Navigate to a URL in the browser. Reroute app names to RUN."""
    url = params.strip()
    
    # Fix 2: Only navigate if it looks like a URL. Otherwise launch as an app.
    if not url.startswith("http") and "." not in url and "/" not in url:
        logger.info(f"BROWSER_NAVIGATE received app name '{url}', rerouting to RUN.")
        from executors.app_actions import handle_run
        return handle_run(url)

    ctrl = _get_controller()
    result = _run_async(ctrl.navigate, url)
    return ActionResult(success=result["success"], message=result["message"])


@register_action("BROWSER_CLICK", "👆 Click Element", "Click element on page", "browser")
def handle_browser_click(params: str) -> ActionResult:
    """Click an element on the current page by description."""
    ctrl = _get_controller()
    result = _run_async(ctrl.click_element, params)
    return ActionResult(success=result["success"], message=result["message"])


@register_action("BROWSER_TYPE", "⌨️ Type in Field", "Type text in a form field", "browser")
def handle_browser_type(params: str) -> ActionResult:
    """Type text into a form field. Params: field_description|text"""
    parts = params.split("|", 1)
    if len(parts) == 2:
        field_desc, text = parts
    else:
        field_desc = "search"
        text = params

    ctrl = _get_controller()
    result = _run_async(ctrl.type_in_field, field_desc, text)
    return ActionResult(success=result["success"], message=result["message"])


@register_action("BROWSER_READ", "📖 Read Page", "Read current page content", "browser")
def handle_browser_read(params: str) -> ActionResult:
    """Extract and return the main text content of the current page."""
    ctrl = _get_controller()
    result = _run_async(ctrl.read_page)
    if result["success"]:
        content = result.get("content", "")
        # Summarize if too long for TTS
        if len(content) > 500:
            summary = content[:500] + "..."
            return ActionResult(success=True, message=f"Page: {result.get('title', '')}. {summary}")
        return ActionResult(success=True, message=f"Page: {result.get('title', '')}. {content}")
    return ActionResult(success=result["success"], message=result["message"])


@register_action("BROWSER_SCROLL", "📜 Scroll Page", "Scroll the browser page", "browser")
def handle_browser_scroll(params: str) -> ActionResult:
    """Scroll the browser page. Params: direction or direction|amount"""
    parts = params.split("|", 1)
    direction = parts[0].strip() if parts else "down"
    amount = int(parts[1].strip()) if len(parts) > 1 and parts[1].strip().isdigit() else 3

    ctrl = _get_controller()
    result = _run_async(ctrl.scroll, direction, amount)
    return ActionResult(success=result["success"], message=result["message"])


@register_action("BROWSER_TAB", "🗂️ Tab Control", "Manage browser tabs", "browser")
def handle_browser_tab(params: str) -> ActionResult:
    """Tab management. Params: action|target (new, close, list, switch|name)"""
    parts = params.split("|", 1)
    action = parts[0].strip() if parts else "list"
    target = parts[1].strip() if len(parts) > 1 else ""

    ctrl = _get_controller()
    result = _run_async(ctrl.manage_tab, action, target)
    return ActionResult(success=result["success"], message=result["message"])


@register_action("BROWSER_BACK", "⬅️ Back", "Navigate back in browser history", "browser")
def handle_browser_back(params: str) -> ActionResult:
    """Go back in browser history."""
    ctrl = _get_controller()
    result = _run_async(ctrl.go_back)
    return ActionResult(success=result["success"], message=result["message"])


@register_action("BROWSER_FORWARD", "➡️ Forward", "Navigate forward in browser history", "browser")
def handle_browser_forward(params: str) -> ActionResult:
    """Go forward in browser history."""
    ctrl = _get_controller()
    result = _run_async(ctrl.go_forward)
    return ActionResult(success=result["success"], message=result["message"])


@register_action("BROWSER_SCREENSHOT", "📸 Page Screenshot", "Screenshot current browser tab", "browser")
def handle_browser_screenshot(params: str) -> ActionResult:
    """Capture a screenshot of the current browser tab."""
    ctrl = _get_controller()
    result = _run_async(ctrl.take_screenshot)
    return ActionResult(success=result["success"], message=result["message"])


@register_action("BROWSER_FILL_FORM", "📝 Fill Form", "Auto-fill form fields", "browser")
def handle_browser_fill_form(params: str) -> ActionResult:
    """Auto-fill form fields using stored profile data."""
    ctrl = _get_controller()
    result = _run_async(ctrl.fill_form, params)
    return ActionResult(success=result["success"], message=result["message"])
