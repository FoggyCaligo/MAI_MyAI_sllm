"""Opt-in JavaScript browser navigation. Sessions are scoped to one MAI run."""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field
from playwright.async_api import Browser, BrowserContext, Page, Playwright, Request, Route, async_playwright

from .registry import ToolRegistry
from .web import _validate_public_url


class BrowserNavigationError(RuntimeError):
    """Browser navigation or session contract failed."""


class BrowserOpenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1)


class BrowserSessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1)


class BrowserClickInput(BrowserSessionInput):
    element_ref: str = Field(min_length=1)


class BrowserScrollInput(BrowserSessionInput):
    pixels: int = Field(ge=-10000, le=10000)


@dataclass
class _Session:
    browser: Browser
    context: BrowserContext
    page: Page
    snapshot_generation: int = 0


# DOM reads are structural; element labels and links come from the rendered page,
# not from guessed URL patterns or interpretation of their wording.
_SNAPSHOT_JS = """
({generation, maxText, maxElements}) => {
  const excluded = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE']);
  const body = document.body;
  if (!body) throw new Error('Document body is missing');
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_ELEMENT);
  let node;
  while ((node = walker.nextNode())) {
    if (node.hasAttribute('data-mai-ref')) node.removeAttribute('data-mai-ref');
  }
  const candidates = Array.from(document.querySelectorAll(
    'a[href],button,input,select,textarea,[role="button"],[role="link"],[tabindex]'
  ));
  const visible = candidates.filter(el => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      rect.width > 0 && rect.height > 0;
  });
  const elements = visible.slice(0, maxElements).map((el, i) => {
    const ref = 'g' + generation + 'e' + i;
    el.setAttribute('data-mai-ref', ref);
    return {
      ref,
      role: el.getAttribute('role') || el.tagName.toLowerCase(),
      text: (el.innerText || el.value || '').trim().slice(0, 300),
      accessible_name: el.getAttribute('aria-label') || el.getAttribute('title') || null,
      href: el.href || null
    };
  });
  const raw = body.innerText || '';
  return {
    url: location.href,
    title: document.title,
    visible_text: raw.slice(0, maxText),
    text_truncated: raw.length > maxText,
    elements,
    elements_truncated: visible.length > maxElements,
    total_elements: visible.length,
    links: Array.from(document.querySelectorAll('a[href]')).map(el => ({
      text: (el.innerText || '').trim().slice(0, 300),
      href: el.href
    })).filter(v => v.href.startsWith('http:') || v.href.startsWith('https:')).slice(0, maxElements)
  };
}
"""


class BrowserSessionManager:
    """Each agent run owns its own isolated browser sessions and cleans them up."""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._sessions: dict[str, _Session] = {}
        self._lock = asyncio.Lock()

    async def _authorize(self, route: Route, request: Request) -> None:
        # Intercept subresource requests and redirects as well as top-level visits.
        # Do not expose file:, data:, localhost or private IP destinations.
        try:
            _validate_public_url(request.url)
        except (ValueError, RuntimeError):
            await route.abort()
            return
        await route.continue_()

    async def open(self, url: str) -> dict[str, Any]:
        _validate_public_url(url)
        async with self._lock:
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            browser = await self._playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context(accept_downloads=False, service_workers="block")
                await context.route("**/*", self._authorize)
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # JS is executed by Chromium. Snapshot can be refreshed after
                # any subsequent asynchronous rendering using browser_snapshot.
                await page.wait_for_timeout(1000)
                session_id = secrets.token_urlsafe(18)
                session = _Session(browser, context, page)
                self._sessions[session_id] = session
                result = await self._snapshot(session)
                return {"session_id": session_id, **result}
            except BaseException:
                await browser.close()
                raise

    def _get(self, session_id: str) -> _Session:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise BrowserNavigationError("Unknown or closed browser session") from exc

    async def _snapshot(self, session: _Session) -> dict[str, Any]:
        session.snapshot_generation += 1
        return await session.page.evaluate(
            _SNAPSHOT_JS,
            {"generation": session.snapshot_generation, "maxText": 16000, "maxElements": 350},
        )

    async def snapshot(self, session_id: str) -> dict[str, Any]:
        return {"session_id": session_id, **await self._snapshot(self._get(session_id))}

    async def click(self, session_id: str, element_ref: str) -> dict[str, Any]:
        session = self._get(session_id)
        if not element_ref.startswith(f"g{session.snapshot_generation}e"):
            raise BrowserNavigationError("Stale element reference: take a new snapshot")
        target = session.page.locator(f'[data-mai-ref="{element_ref}"]')
        if await target.count() != 1:
            raise BrowserNavigationError("Element not present or not unique in current page")
        await target.click(timeout=15000)
        await session.page.wait_for_timeout(500)
        return await self.snapshot(session_id)

    async def scroll(self, session_id: str, pixels: int) -> dict[str, Any]:
        session = self._get(session_id)
        await session.page.mouse.wheel(0, pixels)
        await session.page.wait_for_timeout(500)
        return await self.snapshot(session_id)

    async def close(self, session_id: str) -> dict[str, bool]:
        session = self._sessions.pop(session_id, None)
        if session is None:
            raise BrowserNavigationError("Unknown or closed browser session")
        await session.browser.close()
        return {"closed": True}

    async def close_all(self) -> None:
        sessions = list(self._sessions)
        try:
            for key in sessions:
                await self.close(key)
        finally:
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None


def register_browser_tools(registry: ToolRegistry, manager: BrowserSessionManager) -> None:
    registry.add(name="browser_open", description="Open a public HTTP(S) URL in isolated Chromium, execute JavaScript and return rendered text, actual links, and interactive element refs. Returns session_id. For static pages, use faster web_fetch. Never guess URLs.", input_model=BrowserOpenInput, handler=manager.open, timeout_seconds=60, category="web")
    registry.add(name="browser_snapshot", description="Refresh the rendered DOM, visible text, absolute link URLs, and click element refs for an open browser session. Use after dynamic loading.", input_model=BrowserSessionInput, handler=manager.snapshot, timeout_seconds=30, category="web")
    registry.add(name="browser_click", description="Click an exact element_ref returned by the most recent browser snapshot in this session; never use text guessing. Returns updated snapshot.", input_model=BrowserClickInput, handler=manager.click, timeout_seconds=40, category="web")
    registry.add(name="browser_scroll", description="Scroll a browser page by signed pixels and return an updated DOM snapshot for infinite-scroll content.", input_model=BrowserScrollInput, handler=manager.scroll, timeout_seconds=30, category="web")
    registry.add(name="browser_close", description="Explicitly close a browser session. Every remaining session is also cleaned at the end of the agent run.", input_model=BrowserSessionInput, handler=manager.close, timeout_seconds=20, category="web")
