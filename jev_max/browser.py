"""Playwright browser driver for jev-max.

One browser call per snapshot. Every executed target is resolved from an
observed node; model output never becomes selectors, coordinates, or JS.
Clicks are occlusion-checked before input.
"""

from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

SNAPSHOT_JS = Path(__file__).with_name("snapshot.js").read_text()


class StalePage(Exception):
    """The page changed between decision and action."""


class Browser:
    def __init__(
        self,
        headless: bool = True,
        user_data_dir: str | None = None,
        proxy: dict | None = None,
        viewport: dict | None = None,
        slow_mo: int = 0,
    ):
        self._pw = sync_playwright().start()
        launch_kw: dict = {"headless": headless, "slow_mo": slow_mo}
        if proxy:
            launch_kw["proxy"] = proxy
        if user_data_dir:
            self._ctx = self._pw.chromium.launch_persistent_context(
                user_data_dir,
                viewport=viewport or {"width": 1366, "height": 900},
                **launch_kw,
            )
        else:
            browser = self._pw.chromium.launch(**launch_kw)
            self._ctx = browser.new_context(viewport=viewport or {"width": 1366, "height": 900})
        self._ctx.add_init_script(SNAPSHOT_JS)
        self._tabs: list = []
        self._active = -1

    # -- tabs ------------------------------------------------------------
    @property
    def page(self):
        return self._tabs[self._active]

    def goto(self, url: str):
        if self._active < 0:
            self.new_tab(url)
        else:
            self.page.goto(url, wait_until="domcontentloaded")
        return self.observe()

    def new_tab(self, url: str = "about:blank") -> int:
        page = self._ctx.new_page()
        page.on("popup", lambda pop: self._register_popup(pop))
        if url != "about:blank":
            page.goto(url, wait_until="domcontentloaded")
        self._tabs.append(page)
        self._active = len(self._tabs) - 1
        return self._active

    def _register_popup(self, pop):
        if pop not in self._tabs:
            self._tabs.append(pop)

    def switch_tab(self, index: int):
        self._active = index
        self.page.bring_to_front()

    def close_tab(self, index: int):
        if len(self._tabs) <= 1:
            return
        self._tabs[index].close()
        del self._tabs[index]
        self._active = min(self._active, len(self._tabs) - 1)

    @property
    def tab_count(self) -> int:
        return len(self._tabs)

    # -- observation ------------------------------------------------------
    def observe(self, screenshot: bool = False) -> dict:
        """Atomic snapshot: controls, names, boxes, fingerprint."""
        page = self.page
        try:
            snap = page.evaluate("window.__jev.snapshot()")
        except Exception:  # noqa: BLE001
            # init script may not have run yet (e.g. very first load)
            page.evaluate(SNAPSHOT_JS)
            snap = page.evaluate("window.__jev.snapshot()")
        if snap.get("error"):
            raise RuntimeError(f"snapshot failed: {snap['error']}")
        snap["tab"] = self._active
        snap["tabs"] = len(self._tabs)
        if screenshot:
            snap["screenshot"] = page.screenshot(type="jpeg", quality=70)
        return snap

    def fresh(self, snap: dict) -> bool:
        try:
            cur = self.page.evaluate("window.__jev.snapshot()")
        except Exception:  # noqa: BLE001
            return False
        return cur.get("fingerprint") == snap.get("fingerprint") and cur.get("url") == snap.get("url")

    # -- target resolution -------------------------------------------------
    def _resolve(self, index: int):
        handle = self.page.evaluate_handle(f"window.__jev.node({int(index)})")
        el = handle.as_element()
        if el is None:
            raise StalePage(f"target [{index}] no longer resolves to an element")
        return el

    def _occluded(self, el, in_frame: bool) -> bool:
        """True if another element covers the click point.

        Shadow-aware: elementFromPoint is called on the element's own root
        (shadow roots don't pierce from document). For framed elements the
        point is mapped up through ancestor frames to the top viewport.
        """
        return self.page.evaluate(
            """(el) => {
                const r = el.getBoundingClientRect();
                const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
                // 1. occlusion inside the element's own document/scope
                const root = el.getRootNode();
                const scope = root instanceof ShadowRoot ? root : el.ownerDocument;
                const t = scope.elementFromPoint(cx, cy);
                if (!(t === el || el.contains(t))) return true;
                // 2. map the point to the top-level viewport through frames.
                // (Shadow roots share the outer viewport space: no offset.
                //  Iframes have their own viewport: add the frame offset.)
                let node = el, px = cx, py = cy;
                while (true) {
                    const rd = node.getRootNode();
                    if (rd instanceof ShadowRoot) { node = rd.host; continue; }
                    const win = node.ownerDocument.defaultView;
                    const frameEl = win ? win.frameElement : null;
                    if (!frameEl) break;
                    const fr = frameEl.getBoundingClientRect();
                    px += fr.x; py += fr.y;
                    node = frameEl;
                }
                if (node === el) return false; // top-level document: done
                const top = document.elementFromPoint(px, py);
                return !(top === node || (node.contains && node.contains(top)));
            }""",
            el,
        )

    # -- JS-dispatched primitives (for pierced-frame targets, where ---------
    # Playwright's own hit-test runs in the wrong viewport) ------------------
    @staticmethod
    def _js_click(el):
        el.evaluate("(el) => { el.scrollIntoView({block: 'center'}); el.click(); }")

    def _js_fill(self, el, text: str):
        el.evaluate("(el) => { el.scrollIntoView({block: 'center'}); el.focus(); el.select(); }")
        self.page.keyboard.press("ControlOrMeta+a")
        self.page.keyboard.press("Backspace")
        self.page.keyboard.type(text)

    @staticmethod
    def _js_select(el, label: str) -> bool:
        return el.evaluate(
            """(el, label) => {
                const opts = Array.from(el.options || []);
                const norm = (s) => (s || '').trim();
                const opt = opts.find((o) => norm(o.label || o.text) === norm(label))
                    || opts.find((o) => norm(o.label || o.text).includes(norm(label)));
                if (!opt) return false;
                el.value = opt.value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }""",
            label,
        )

    # -- actions -----------------------------------------------------------
    def act(self, action: dict, page_snap: dict, text: str | None = None,
            drag_target: dict | None = None, file_path: str | None = None) -> dict:
        """Execute one indexed action. Returns an execution report."""
        if not self.fresh(page_snap):
            raise StalePage("page changed before action")
        kind = action["kind"]
        el = self._resolve(action["id"])
        framed = bool(action.get("inFrame"))
        started = time.perf_counter()

        if kind == "click":
            if self._occluded(el, framed):
                return {"ok": False, "reason": "occluded"}
            if framed:
                self._js_click(el)
            else:
                el.scroll_into_view_if_needed(timeout=3000)
                el.click(timeout=5000)
        elif kind == "fill":
            if text is None:
                raise ValueError("fill needs text")
            if framed:
                self._js_fill(el, text)
            else:
                try:
                    el.fill(text, timeout=5000)
                except PlaywrightTimeout:
                    el.click(timeout=5000)
                    el.type(text, timeout=10000)
        elif kind == "select":
            if text is None:
                raise ValueError("select needs option label text")
            if framed:
                if not self._js_select(el, text):
                    return {"ok": False, "reason": "option_not_found"}
            else:
                el.select_option(label=text, timeout=5000)
        elif kind == "check":
            if self._occluded(el, framed):
                return {"ok": False, "reason": "occluded"}
            if framed:
                self._js_click(el)  # native toggle
            elif not action.get("checked"):
                el.scroll_into_view_if_needed(timeout=3000)
                el.check(timeout=5000)
            else:
                el.scroll_into_view_if_needed(timeout=3000)
                el.uncheck(timeout=5000)
        elif kind == "upload":
            if not file_path:
                raise ValueError("upload needs file_path")
            el.set_input_files(file_path, timeout=10000)
        elif kind == "drag":
            if drag_target is None:
                raise ValueError("drag needs drag_target")
            src_box = el.bounding_box() or {}
            tgt = self._resolve(drag_target["id"])
            tgt.scroll_into_view_if_needed(timeout=3000)
            tgt_box = tgt.bounding_box() or {}
            m = self.page.mouse
            m.move(src_box["x"] + src_box["width"] / 2, src_box["y"] + src_box["height"] / 2)
            m.down()
            m.move(tgt_box["x"] + tgt_box["width"] / 2, tgt_box["y"] + tgt_box["height"] / 2, steps=12)
            m.up()
        elif kind == "frame":
            return {"ok": False, "reason": "cross_origin_frame",
                    "box": action.get("box"), "name": action.get("name")}
        else:
            raise ValueError(f"unknown action kind: {kind}")

        ms = round((time.perf_counter() - started) * 1000)
        return {"ok": True, "latency_ms": ms}

    def press(self, key: str):
        self.page.keyboard.press(key)

    def scroll_page(self, direction: str, amount: int = 600):
        delta = amount if direction == "down" else -amount
        self.page.evaluate(f"window.scrollBy(0, {delta})")

    def scroll_container(self, index: int, direction: str, amount: int = 400):
        el = self._resolve(index)
        delta = amount if direction == "down" else -amount
        self.page.evaluate(f"(el) => el.scrollBy(0, {delta})", el)

    def wait(self, ms: int = 500):
        self.page.wait_for_timeout(ms)

    def close(self):
        try:
            self._ctx.close()
        finally:
            self._pw.stop()
