"""Piercing snapshot tests: shadow DOM + same-origin iframes.

No API keys needed: these exercise snapshot.js and browser.py only.
"""

import pytest

from jev_max.browser import Browser

FIXTURE = "file:///home/hatch/workspace/jev-max/tests/fixtures/shadow.html"


@pytest.fixture()
def browser():
    b = Browser(headless=True)
    yield b
    b.close()


def test_shadow_dom_pierced(browser):
    snap = browser.goto(FIXTURE)
    shadow = [a for a in snap["actions"] if a.get("inShadow")]
    assert len(shadow) == 2, f"expected 2 shadow controls, got {snap['stats']}"
    kinds = {a["kind"] for a in shadow}
    assert kinds == {"fill", "click"}


def test_iframe_pierced(browser):
    snap = browser.goto(FIXTURE)
    framed = [a for a in snap["actions"] if a.get("inFrame") and not a.get("crossOrigin")]
    assert len(framed) == 1
    assert framed[0]["name"] == "Confirm inside frame"


def test_shadow_fill_and_click(browser):
    snap = browser.goto(FIXTURE)
    fill = next(a for a in snap["actions"] if a["kind"] == "fill")
    assert browser.act(fill, snap, text="Ada")["ok"]
    snap2 = browser.observe()
    btn = next(a for a in snap2["actions"] if a["name"] == "Greet")
    assert browser.act(btn, snap2)["ok"]
    assert browser.page.evaluate("document.getElementById('result').textContent") == "Hello, Ada"


def test_pierced_frame_click(browser):
    snap = browser.goto(FIXTURE)
    btn = next(a for a in snap["actions"] if a.get("inFrame"))
    assert browser.act(btn, snap)["ok"]
    assert browser.page.title() == "confirmed by frame"


def test_occlusion_rejected(browser):
    browser.goto(FIXTURE)
    browser.page.evaluate(
        """() => {
            const d = document.createElement('div');
            d.id = 'overlay';
            d.style.cssText = 'position:fixed;inset:0;background:white;z-index:9999';
            document.body.appendChild(d);
        }"""
    )
    snap = browser.observe()
    btn = next(a for a in snap["actions"] if a["name"] == "Greet")
    assert browser.act(btn, snap) == {"ok": False, "reason": "occluded"}


def test_render_table_groups_heads():
    from jev_max.model import render_table

    actions = [
        {"id": 1, "kind": "click", "role": "button", "name": "Go", "disabled": False},
        {"id": 2, "kind": "fill", "role": "textbox", "name": "Search", "disabled": False},
        {"id": 3, "kind": "click", "role": "button", "name": "Hidden", "disabled": True},
    ]
    table, heads = render_table(actions)
    assert "[1] button Go" in table
    assert "[2] textbox Search" in table
    assert "Hidden" not in table  # disabled controls are not offered
    assert [a["id"] for a in heads["click_target"]] == [1]
    assert [a["id"] for a in heads["fill_target"]] == [2]
