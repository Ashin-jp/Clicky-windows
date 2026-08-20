"""
browser_controller.py — Playwright-based browser automation.

Maintains a dedicated Chromium instance separate from the user's Chrome.
Uses a persistent user_data_dir in %APPDATA%/Clicky/browser_profile/
so logins, cookies, and sessions survive restarts.
"""

import asyncio
import logging
import os
import threading
import urllib.parse
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Persistent profile directory
BROWSER_PROFILE_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "Clicky", "browser_profile"
)


class BrowserController:
    """
    Controls a dedicated Playwright Chromium instance.
    The instance is launched on first use and kept alive for Clicky's lifetime.
    """

    def __init__(self, tts_callback=None):
        self._tts_callback = tts_callback
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._lock = threading.Lock()
        self._ready = False
        self._launch_failed = False
        self._error_msg = ""
        self._login_prompted = False  # Prevent repeated login prompts per session
        self._auth_file = os.path.join(BROWSER_PROFILE_DIR, "auth.json")
        self._login_done_event = threading.Event()

    def signal_login_done(self):
        """Set the event when user confirms login via 'done'."""
        self._login_done_event.set()
        
        # Make sure we save the new auth state right after login confirmation
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = loop.create_task(self.save_auth_state())
                task.add_done_callback(lambda t: logger.error(f"Auth save failed: {t.exception()}") if not t.cancelled() and t.exception() else None)
        except Exception:
            pass

        # Ensure profile dir exists
        os.makedirs(BROWSER_PROFILE_DIR, exist_ok=True)
        logger.info(f"BrowserController: profile dir = {BROWSER_PROFILE_DIR}")



    async def ensure_browser(self) -> bool:
        """Ensure the browser is launched and ready. Returns True if ready."""
        if self._ready and self._page and not self._page.is_closed():
            return True

        if self._launch_failed:
            return False

        try:
            import playwright
            from playwright.async_api import async_playwright
        except ImportError:
            self._launch_failed = True
            self._error_msg = "Playwright is not installed"
            logger.error("Playwright not installed. Run: pip install playwright, then python -m playwright install chromium")
            if self._tts_callback:
                import asyncio
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(
                    lambda: self._tts_callback("Playwright is not installed. Please run: pip install playwright, then python dash m playwright install chromium")
                )
            return False

        try:
            if not self._playwright:
                self._playwright = await async_playwright().start()

            # Launch browser instance
            self._browser = await self._playwright.chromium.launch(
                headless=False,
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-infobars",
                    "--disable-blink-features=AutomationControlled",
                ],
                ignore_default_args=["--enable-automation"],
            )

            # Load persistent context if auth file exists
            if os.path.exists(self._auth_file):
                self._context = await self._browser.new_context(
                    storage_state=self._auth_file,
                    viewport={"width": 1280, "height": 900}
                )
            else:
                self._context = await self._browser.new_context(
                    viewport={"width": 1280, "height": 900}
                )

            # Use existing page or create one
            self._page = await self._context.new_page()
            self._page.on("close", self._on_page_closed)

            self._ready = True
            logger.info("BrowserController: Chromium launched (dedicated instance)")
            
            # Handle first-run or expired login
            await self._handle_first_run_login()

            return True

        except Exception as e:
            self._launch_failed = True
            self._error_msg = str(e)
            logger.error(f"BrowserController: launch failed: {e}")
            return False

    def _on_page_closed(self, page):
        """Handler for when a page is closed. Automatically updates self._page."""
        try:
            if self._context:
                pages = [p for p in self._context.pages if not p.is_closed()]
                if pages:
                    self._page = pages[-1]
                else:
                    self._page = None
                    import asyncio
                    try:
                        from companion_manager import create_tracked_task
                        create_tracked_task(self._recover_page(), "browser_recover_page")
                    except Exception:
                        pass
        except Exception:
            pass

    async def _recover_page(self):
        """Recover or recreate self._page if it's dead."""
        if not self._context:
            await self.ensure_browser()
            return
            
        pages = [p for p in self._context.pages if not p.is_closed()]
        if pages:
            self._page = pages[-1]
            try:
                self._page.on("close", self._on_page_closed)
            except Exception:
                pass
        else:
            self._page = await self._context.new_page()
            self._page.on("close", self._on_page_closed)

    async def _get_valid_page(self):
        """Ensure self._page is valid and not closed."""
        if not self._page or self._page.is_closed():
            await self._recover_page()
        return self._page

    async def _handle_first_run_login(self):
        """Check login at session start and block/prompt if necessary."""
        is_logged_in = await self.verify_login("youtube")
        if not is_logged_in:
            logger.info("BrowserController: User is not logged in. Prompting.")
            from companion_manager import get_companion_manager
            cm = get_companion_manager()
            if cm:
                import asyncio
                asyncio.run_coroutine_threadsafe(
                    cm.tts.speak_text("The Clicky browser needs you to log in once. Please log into your Google account in the Clicky browser window, then say done or press Enter."),
                    cm._event_loop
                )
            
            # Wait for user confirmation (Event set by signal_login_done)
            import asyncio
            self._login_done_event.clear()
            await asyncio.to_thread(self._login_done_event.wait)
            
            # Once done, save the state
            await self.save_auth_state()
            logger.info("BrowserController: First-run login completed and saved.")

    async def verify_connection(self) -> bool:
        """Verify the browser is connected and responsive, reconnecting if needed."""
        if not await self.ensure_browser():
            return False
            
        try:
            if not self._context or not self._context.pages:
                self._ready = False
                return await self.ensure_browser()
                
            await self._get_valid_page()
            return True
        except Exception:
            self._ready = False
            return await self.ensure_browser()

    async def verify_login(self, site: str = "youtube") -> bool:
        """Check if user is logged in. If not, prompt for manual login and save session."""
        page = await self._get_valid_page()
        if not page:
            return True

        try:
            if site == "youtube":
                if "youtube.com" not in page.url:
                    await page.goto("https://www.youtube.com", wait_until="domcontentloaded")
                
                # Check for avatar button = logged in
                avatar = page.locator("#avatar-btn, img.yt-img-shadow[alt='Avatar image']")
                if await avatar.count() > 0:
                    return True
                # Check for Sign In button = NOT logged in
                sign_in = page.locator("a[aria-label='Sign in'], tp-yt-paper-button#button:has-text('Sign in')")
                if await sign_in.count() > 0 and not self._login_prompted:
                    self._login_prompted = True
                    return False
            elif site == "google":
                if "google.com" not in page.url:
                    await page.goto("https://www.google.com", wait_until="domcontentloaded")
                avatar = page.locator("a[aria-label*='Account'], img.gb_A")
                if await avatar.count() > 0:
                    return True
                if not self._login_prompted:
                    self._login_prompted = True
                    return False
        except Exception as e:
            logger.debug(f"BrowserController: login check failed: {e}")
        return True  # Default: assume logged in to avoid blocking

    async def save_auth_state(self):
        """Save current browser session cookies/state to disk."""
        try:
            if self._context:
                await self._context.storage_state(path=self._auth_file)
                logger.info(f"BrowserController: auth state saved to {self._auth_file}")
        except Exception as e:
            logger.error(f"BrowserController: failed to save auth: {e}")

    async def search_on_site(self, query: str, domain: str = None) -> dict:
        """
        Universal site search. Uses URL-pattern shortcut if available,
        otherwise auto-detects the search box via layered strategies.
        """
        if not await self.verify_connection():
            return {"success": False, "message": self._get_fail_message()}

        from site_search import get_site_search, SiteSearchManager
        ssm = get_site_search()

        page = await self._get_valid_page()

        # Resolve domain
        if domain:
            domain = ssm.resolve_domain(domain)
        else:
            # Use current page domain
            domain = SiteSearchManager.extract_domain(page.url)
            if not domain or domain in ("", "about:blank", "newtab"):
                domain = "google.com"

        # Fix 3: YouTube exclusive URL shortcut
        if domain == "youtube.com":
            try:
                from urllib.parse import quote_plus
                url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
                logger.info(f"SiteSearch: YouTube URL shortcut → {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page = await self._get_valid_page()
                
                try:
                    await page.wait_for_selector("ytd-video-renderer, ytd-rich-item-renderer", state="visible", timeout=10000)
                except Exception:
                    logger.debug("SiteSearch: ytd-video-renderer wait timed out, continuing")
                    
                titles = await self._extract_page_titles(domain)
                if titles:
                    summary = ", ".join(titles[:3])
                    return {"success": True, "message": f"Found results for {query}: {summary}."}
                return {"success": True, "message": f"Found results for {query} on YouTube."}
            except Exception as e:
                logger.error(f"YouTube search failed: {e}")
                return {"success": False, "message": f"YouTube search failed: {e}"}

        profile = ssm.get_profile(domain)
        logger.info(f"SiteSearch: query='{query}' domain='{domain}' profile={'yes' if profile else 'no'}")

        try:
            # ── Layer 3: URL-pattern shortcut (fastest) ──────────────
            if profile and profile.get("url_pattern"):
                from urllib.parse import quote_plus
                url = profile["url_pattern"].replace("QUERY", quote_plus(query))
                logger.info(f"SiteSearch: URL shortcut → {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page = await self._get_valid_page()

                # Wait for result elements if known
                if profile.get("result_wait"):
                    try:
                        await page.wait_for_selector(
                            profile["result_wait"], state="visible", timeout=10000
                        )
                    except Exception:
                        logger.debug("SiteSearch: result_wait timed out, continuing")

                # Extract readable results
                titles = await self._extract_page_titles(domain)
                if titles:
                    summary = ". ".join([f"{i+1}. {t}" for i, t in enumerate(titles[:3])])
                    return {"success": True, "message": f"Searched {domain} for {query}. {summary}"}
                return {"success": True, "message": f"Searched {domain} for {query}. Results loaded."}

            # ── Layer 1+2: DOM-based search ──────────────────────────
            # Navigate to site if not already there
            current_domain = SiteSearchManager.extract_domain(page.url)
            if domain not in current_domain:
                await page.goto(f"https://www.{domain}", wait_until="domcontentloaded", timeout=15000)
                page = await self._get_valid_page()
                await page.wait_for_timeout(1500)

            # Build selector priority list
            selectors = []
            # Profile selectors first (learned/pre-populated)
            if profile and profile.get("selector_list"):
                selectors.extend(profile["selector_list"])
            # Universal detection strategies
            selectors.extend([
                "input[type='search']",
                "[role='search'] input",
                "input[name='q']",
                "textarea[name='q']",
                "input[name='s']",         # WordPress
                "input[name='query']",
                "#search", "#searchInput", "#q",
                ".search-input input", ".search-bar input",
                "[role='combobox']",
            ])
            # Deduplicate while preserving order
            seen = set()
            unique_selectors = []
            for s in selectors:
                if s not in seen:
                    seen.add(s)
                    unique_selectors.append(s)

            # Try each selector
            search_box = None
            winning_selector = None
            for sel in unique_selectors:
                try:
                    locator = page.locator(sel).first
                    if await locator.is_visible(timeout=1500):
                        search_box = locator
                        winning_selector = sel
                        break
                except Exception:
                    continue

            # Fallback: find largest visible text input
            if not search_box:
                search_box, winning_selector = await self._find_largest_input()

            if not search_box:
                return {"success": False, "message": f"Couldn't find a search box on {domain}."}

            # Click to focus, clear, type, submit
            logger.info(f"SiteSearch: using selector '{winning_selector}' on {domain}")
            await search_box.click()
            await page.wait_for_timeout(300)
            await search_box.fill("")
            await search_box.fill(query)
            await page.keyboard.press("Enter")

            # Wait for results
            if profile and profile.get("result_wait"):
                try:
                    await page.wait_for_selector(
                        profile["result_wait"], state="visible", timeout=10000
                    )
                except Exception:
                    pass
            else:
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    await page.wait_for_timeout(3000)

            # Learn: save the working selector for next time
            if winning_selector:
                ssm.save_profile(domain, winning_selector)

            titles = await self._extract_page_titles(domain)
            if titles:
                summary = ". ".join([f"{i+1}. {t}" for i, t in enumerate(titles[:3])])
                return {"success": True, "message": f"Searched {domain} for {query}. {summary}"}
            return {"success": True, "message": f"Searched {domain} for {query}. Results loaded."}

        except Exception as e:
            logger.error(f"SiteSearch failed on {domain}: {e}")
            return {"success": False, "message": f"Search failed on {domain}: {e}"}

    async def _find_largest_input(self):
        """Fallback: find the largest visible text input that isn't a password field."""
        try:
            inputs = self._page.locator(
                "input[type='text']:visible, input[type='search']:visible, "
                "input:not([type]):visible, textarea:visible"
            )
            count = await inputs.count()
            best = None
            best_width = 0
            for i in range(count):
                el = inputs.nth(i)
                try:
                    box = await el.bounding_box(timeout=1000)
                    if box and box["width"] > best_width:
                        # Skip if inside a login form
                        parent_form = await el.evaluate(
                            "el => el.closest('form')?.action || ''"
                        )
                        if "login" in str(parent_form).lower() or "signin" in str(parent_form).lower():
                            continue
                        best_width = box["width"]
                        best = el
                except Exception:
                    continue
            return best, "auto-detected-largest-input" if best else (None, None)
        except Exception:
            return None, None

    async def _extract_page_titles(self, domain: str) -> list[str]:
        """Extract result titles from a search results page (site-aware)."""
        titles = []
        try:
            # YouTube
            if "youtube" in domain:
                items = self._page.locator("ytd-video-renderer #video-title, ytd-rich-item-renderer #video-title")
            # Google
            elif "google" in domain:
                items = self._page.locator("div.g h3, div[data-sokoban-container] h3")
            # Reddit
            elif "reddit" in domain:
                items = self._page.locator("a[data-testid='post-title'], .Post h3, faceplate-screen-reader-content")
            # GitHub
            elif "github" in domain:
                items = self._page.locator(".search-title a, .Box-row .f4 a")
            # Generic: try common result patterns
            else:
                items = self._page.locator(
                    "h2 a, h3 a, .result-title, .search-result a, "
                    "[class*='result'] h2, [class*='result'] h3"
                )

            count = await items.count()
            for i in range(min(count, 5)):
                try:
                    text = await items.nth(i).inner_text(timeout=2000)
                    if text.strip() and len(text.strip()) > 3:
                        titles.append(text.strip()[:100])
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"SiteSearch: title extraction failed on {domain}: {e}")
        return titles

    async def search(self, query: str) -> dict:
        """Default search — routes through search_on_site with Google."""
        return await self.search_on_site(query, domain="google.com")

    async def search_youtube(self, query: str) -> dict:
        """YouTube search — routes through search_on_site."""
        return await self.search_on_site(query, domain="youtube.com")

    async def navigate(self, url: str) -> dict:
        """Navigate to a URL."""
        if not await self.verify_connection():
            return {"success": False, "message": self._get_fail_message()}

        try:
            page = await self._get_valid_page()
            if not url.startswith(("http://", "https://")):
                url = f"https://{url}"

            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            title = await page.title()
            return {"success": True, "message": f"Opened {title or url}"}

        except Exception as e:
            return {"success": False, "message": f"Navigation failed: {e}"}

    async def click_element(self, description: str) -> dict:
        """Click an element by its text content or role description."""
        if not await self.verify_connection():
            return {"success": False, "message": self._get_fail_message()}

        try:
            page = await self._get_valid_page()
            # Try multiple strategies to find the element
            strategies = [
                # By visible text
                f"text={description}",
                # By role with name
                f"role=link[name=/{description}/i]",
                f"role=button[name=/{description}/i]",
                # By aria-label
                f"[aria-label*='{description}' i]",
                # By title attribute
                f"[title*='{description}' i]",
                # By placeholder
                f"[placeholder*='{description}' i]",
            ]

            for selector in strategies:
                try:
                    locator = page.locator(selector).first
                    if await locator.count() > 0:
                        await locator.click(timeout=5000)
                        await page.wait_for_timeout(1000)
                        return {"success": True, "message": f"Clicked on '{description}'"}
                except Exception:
                    continue

            return {"success": False, "message": f"Couldn't find element matching '{description}'"}

        except Exception as e:
            return {"success": False, "message": f"Click failed: {e}"}

    async def type_in_field(self, field_description: str, text: str) -> dict:
        """Find an input field and type text into it."""
        if not await self.verify_connection():
            return {"success": False, "message": self._get_fail_message()}

        try:
            page = await self._get_valid_page()
            strategies = [
                f"[placeholder*='{field_description}' i]",
                f"[aria-label*='{field_description}' i]",
                f"[name*='{field_description}' i]",
                f"role=textbox[name=/{field_description}/i]",
                f"role=searchbox[name=/{field_description}/i]",
                f"input[type='text']",
                f"input[type='search']",
                f"textarea",
            ]

            for selector in strategies:
                try:
                    locator = page.locator(selector).first
                    if await locator.count() > 0:
                        await locator.click(timeout=3000)
                        await locator.fill(text, timeout=3000)
                        return {"success": True, "message": f"Typed in '{field_description}'"}
                except Exception:
                    continue

            return {"success": False, "message": f"Couldn't find input field '{field_description}'"}

        except Exception as e:
            return {"success": False, "message": f"Type failed: {e}"}

    async def read_page(self) -> dict:
        """Extract main text content from the current page."""
        if not await self.verify_connection():
            return {"success": False, "message": self._get_fail_message()}

        try:
            page = await self._get_valid_page()
            # Try to get main content via common selectors
            content = ""
            for selector in ["main", "article", "[role='main']", "#content", ".content", "body"]:
                try:
                    locator = page.locator(selector).first
                    if await locator.count() > 0:
                        content = await locator.inner_text(timeout=5000)
                        if len(content.strip()) > 100:
                            break
                except Exception:
                    continue

            if not content:
                content = await page.inner_text("body", timeout=5000)

            # Truncate to reasonable length
            content = content.strip()
            if len(content) > 5000:
                content = content[:5000] + "..."

            title = await page.title()
            return {
                "success": True,
                "message": f"Read page: {title}",
                "content": content,
                "title": title,
                "url": page.url,
            }

        except Exception as e:
            return {"success": False, "message": f"Read failed: {e}"}

    async def scroll(self, direction: str = "down", amount: int = 3) -> dict:
        """Scroll the current page."""
        if not await self.verify_connection():
            return {"success": False, "message": self._get_fail_message()}

        try:
            page = await self._get_valid_page()
            pixels = amount * 300
            if direction.lower() == "up":
                pixels = -pixels

            await page.evaluate(f"window.scrollBy(0, {pixels})")
            return {"success": True, "message": f"Scrolled {direction}"}

        except Exception as e:
            return {"success": False, "message": f"Scroll failed: {e}"}

    async def manage_tab(self, action: str, target: str = "") -> dict:
        """Tab management: new, close, switch, list."""
        if not await self.verify_connection():
            return {"success": False, "message": self._get_fail_message()}

        try:
            action = action.lower()

            if action == "new":
                self._page = await self._context.new_page()
                self._page.on("close", self._on_page_closed)
                if target:
                    await self._page.goto(target, wait_until="domcontentloaded", timeout=15000)
                return {"success": True, "message": "Opened new tab"}

            elif action == "close":
                page = await self._get_valid_page()
                if len(self._context.pages) > 1:
                    await page.close()
                    await self._get_valid_page()
                    return {"success": True, "message": "Closed tab"}
                return {"success": False, "message": "Can't close the last tab"}

            elif action == "list":
                tabs = []
                for i, page in enumerate(self._context.pages):
                    title = await page.title()
                    tabs.append(f"{i+1}. {title or page.url}")
                msg = "Open tabs: " + ", ".join(tabs)
                return {"success": True, "message": msg}

            elif action == "switch":
                for page in self._context.pages:
                    title = await page.title()
                    if target.lower() in title.lower() or target.lower() in urllib.parse.unquote_plus(page.url).lower():
                        self._page = page
                        await page.bring_to_front()
                        return {"success": True, "message": f"Switched to {title}"}
                return {"success": False, "message": f"No tab matching '{target}'"}

            return {"success": False, "message": f"Unknown tab action: {action}"}

        except Exception as e:
            return {"success": False, "message": f"Tab action failed: {e}"}

    async def go_back(self) -> dict:
        """Navigate back in history."""
        if not await self.verify_connection():
            return {"success": False, "message": self._get_fail_message()}
        try:
            await self._page.go_back(wait_until="domcontentloaded", timeout=10000)
            title = await self._page.title()
            return {"success": True, "message": f"Went back to {title}"}
        except Exception as e:
            return {"success": False, "message": f"Back failed: {e}"}

    async def go_forward(self) -> dict:
        """Navigate forward in history."""
        if not await self.verify_connection():
            return {"success": False, "message": self._get_fail_message()}
        try:
            await self._page.go_forward(wait_until="domcontentloaded", timeout=10000)
            title = await self._page.title()
            return {"success": True, "message": f"Went forward to {title}"}
        except Exception as e:
            return {"success": False, "message": f"Forward failed: {e}"}

    async def take_screenshot(self) -> dict:
        """Take a screenshot of the current page."""
        if not await self.verify_connection():
            return {"success": False, "message": self._get_fail_message()}
        try:
            import base64
            screenshot_bytes = await self._page.screenshot(type="jpeg", quality=80)
            b64 = base64.b64encode(screenshot_bytes).decode()
            return {
                "success": True,
                "message": "Browser screenshot captured",
                "image_b64": b64,
            }
        except Exception as e:
            return {"success": False, "message": f"Screenshot failed: {e}"}

    async def fill_form(self, context: str = "") -> dict:
        """Auto-fill form fields on the current page using stored profile data."""
        if not await self.verify_connection():
            return {"success": False, "message": self._get_fail_message()}

        try:
            from browser_profile import get_browser_profile_manager
            pm = get_browser_profile_manager()
            form_data = pm.get_form_data()

            if not form_data:
                return {"success": False,
                        "message": "No form data saved. Tell me your name and email to save."}

            filled = 0
            # Common field mappings
            field_map = {
                "name": ["name", "full-name", "fullname", "your-name"],
                "email": ["email", "e-mail", "mail"],
                "phone": ["phone", "tel", "telephone", "mobile"],
                "address": ["address", "street"],
                "city": ["city"],
                "company": ["company", "organization", "org"],
            }

            for data_key, selectors in field_map.items():
                if data_key not in form_data:
                    continue
                value = form_data[data_key]
                for sel_name in selectors:
                    try:
                        for attr in ["name", "id", "placeholder", "aria-label"]:
                            locator = self._page.locator(
                                f"input[{attr}*='{sel_name}' i], textarea[{attr}*='{sel_name}' i]"
                            ).first
                            if await locator.count() > 0:
                                await locator.fill(value, timeout=2000)
                                filled += 1
                                break
                    except Exception:
                        continue

            if filled > 0:
                return {"success": True, "message": f"Filled {filled} form fields. Please review before submitting."}
            return {"success": False, "message": "Couldn't find matching form fields on this page."}

        except Exception as e:
            return {"success": False, "message": f"Form fill failed: {e}"}

    async def shutdown(self):
        """Close the browser and cleanup."""
        try:
            if self._context:
                await self._context.close()
            if self._playwright:
                await self._playwright.stop()
            self._ready = False
            self._page = None
            self._context = None
            self._playwright = None
            logger.info("BrowserController: shutdown complete")
        except Exception as e:
            logger.error(f"BrowserController: shutdown error: {e}")

    # ─── Private Helpers ──────────────────────────────────────────────

    async def _extract_search_results(self) -> list[dict]:
        """Extract search results from Google results page."""
        results = []
        try:
            # Google search result selectors
            items = self._page.locator("div.g, div[data-sokoban-container]")
            count = await items.count()

            for i in range(min(count, 5)):
                try:
                    item = items.nth(i)
                    # Get title
                    title_el = item.locator("h3").first
                    if await title_el.count() == 0:
                        continue
                    title = await title_el.inner_text(timeout=2000)

                    # Get link
                    link_el = item.locator("a").first
                    href = await link_el.get_attribute("href", timeout=2000) if await link_el.count() > 0 else ""

                    # Get snippet
                    snippet = ""
                    for sel in [".VwiC3b", "[data-sncf]", ".IsZvec"]:
                        snippet_el = item.locator(sel).first
                        if await snippet_el.count() > 0:
                            snippet = await snippet_el.inner_text(timeout=2000)
                            break

                    results.append({
                        "title": title,
                        "url": href or "",
                        "snippet": snippet[:200] if snippet else "",
                    })
                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"BrowserController: result extraction failed: {e}")

        return results

    def _get_fail_message(self) -> str:
        if self._error_msg:
            return f"Browser couldn't start: {self._error_msg}. Try running 'playwright install chromium'."
        return "I need to open a browser window to do this. Should I try again?"


# ─── Singleton ────────────────────────────────────────────────────────
_instance: Optional[BrowserController] = None


def get_browser_controller(tts_callback=None) -> BrowserController:
    global _instance
    if _instance is None:
        _instance = BrowserController(tts_callback)
    elif tts_callback and not _instance._tts_callback:
        # Update callback if it was created without one initially
        _instance._tts_callback = tts_callback
    return _instance
