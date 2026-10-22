"""Confident interactions with Next, Load More, and infinite scroll."""

from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.sync_api import Locator, Page

_NEXT_NAME = re.compile(
    r"^(?:go to )?(?:next|next page|load more|more results|show more)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClickResult:
    """What happened when the scanner looked for one pagination control."""

    clicked: bool = False
    ambiguous: bool = False
    disabled: bool = False
    message: str | None = None


def click_next_control(page: Page) -> ClickResult:
    """Click one visible Next or Load More control.

    Several unrelated matches are left alone and reported.
    """
    visible: list[Locator] = []
    disabled = 0
    for role in ("link", "button"):
        locator = page.get_by_role(role, name=_NEXT_NAME)
        count = min(locator.count(), 8)
        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible():
                    continue
                if not item.is_enabled():
                    disabled += 1
                    continue
                visible.append(item)
            except Exception:
                continue
    rel = page.locator("a[rel='next']")
    rel_count = min(rel.count(), 4)
    for index in range(rel_count):
        item = rel.nth(index)
        try:
            if item.is_visible() and item.is_enabled():
                visible.append(item)
        except Exception:
            continue
    unique = _unique_locators(visible)
    if not unique:
        if disabled:
            return ClickResult(
                disabled=True,
                message="The Next control is disabled, so there is no further page to open.",
            )
        return ClickResult()
    if len(unique) > 1:
        inside = [item for item in unique if _inside_pagination(item)]
        if len(inside) != 1:
            return ClickResult(
                ambiguous=True,
                message="More than one Next control was visible, so none were clicked.",
            )
        unique = inside
    try:
        unique[0].click(timeout=5_000)
    except Exception:
        return ClickResult(message="The Next control could not be clicked.")
    return ClickResult(clicked=True)


def scroll_once(page: Page) -> int:
    """Scroll by about one viewport and return the document height."""
    page.evaluate(
        """() => {
            const distance = Math.max(window.innerHeight || 0, 600);
            window.scrollBy(0, distance);
            return document.documentElement.scrollHeight || document.body.scrollHeight || 0;
        }"""
    )
    height = page.evaluate(
        "() => document.documentElement.scrollHeight || document.body.scrollHeight || 0"
    )
    return int(height or 0)


def document_height(page: Page) -> int:
    """Return the current document height."""
    height = page.evaluate(
        "() => document.documentElement.scrollHeight || document.body.scrollHeight || 0"
    )
    return int(height or 0)


def _inside_pagination(locator: Locator) -> bool:
    try:
        return bool(
            locator.evaluate(
                """element => {
                    let current = element;
                    for (let step = 0; step < 8 && current; step += 1) {
                        const label = [
                            current.getAttribute && current.getAttribute('aria-label'),
                            current.id,
                            current.getAttribute && current.getAttribute('role'),
                            current.className,
                        ].join(' ');
                        if (/paginat|pager|\\bpages\\b/i.test(label)) {
                            return true;
                        }
                        current = current.parentElement;
                    }
                    return false;
                }"""
            )
        )
    except Exception:
        return False


def _unique_locators(items: list[Locator]) -> list[Locator]:
    unique: list[Locator] = []
    seen: set[str] = set()
    for item in items:
        try:
            identity = item.evaluate(
                """element => {
                    const href = element.getAttribute && element.getAttribute('href');
                    const text = (element.innerText || element.getAttribute('aria-label') || '').trim();
                    return (href || '') + '|' + text;
                }"""
            )
        except Exception:
            identity = str(id(item))
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    return unique
