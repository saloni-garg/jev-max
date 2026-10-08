"""The jev-max agent loop.

observe -> Jev decides (one request, speculative target heads) -> act ->
repeat. A small LLM only writes text for FILL/SELECT. Canvas fallback
grounds coordinates when the element table has no usable target.
DONE is never trusted: an independent verifier re-checks the outcome.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .browser import Browser, StalePage
from .canvas import GroundingError, ground, to_pixels
from .model import JevClient, JevError, choose, choose_option, field_text, verify_done


class Agent:
    def __init__(
        self,
        url: str,
        goal: str,
        *,
        provider: str | None = None,
        headless: bool = True,
        max_steps: int = 25,
        min_confidence: float = 0.0,
        record_dir: str | Path | None = None,
        upload_file: str | None = None,
        verify: bool = True,
        canvas_fallback: bool = True,
        user_data_dir: str | None = None,
        proxy: dict | None = None,
    ):
        goal = goal.strip()
        if not goal:
            raise ValueError("supply a goal")
        self.goal = goal
        self.max_steps = max_steps
        self.min_confidence = min_confidence
        self.upload_file = upload_file
        self.verify = verify
        self.canvas_fallback = canvas_fallback
        self.record_dir = Path(record_dir) if record_dir else None
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)

        self.client = JevClient(provider=provider)
        self.browser = Browser(headless=headless, user_data_dir=user_data_dir, proxy=proxy)
        self.browser.goto(url)

        self.history: list[dict] = []
        self.status = "ready"
        self.started_at: float | None = None
        self._verify_failures = 0
        self._fallback_uses = 0
        self._decision = None
        self._page = self.browser.observe()

    # -- context manager -------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        self.browser.close()

    # -- recording --------------------------------------------------------
    def _record(self, event: str, payload: dict):
        if not self.record_dir:
            return
        line = {"t_ms": self._elapsed(), "event": event, **payload}
        with open(self.record_dir / "trace.jsonl", "a") as f:
            f.write(json.dumps(line) + "\n")

    def _elapsed(self) -> int:
        if self.started_at is None:
            return 0
        return round((time.perf_counter() - self.started_at) * 1000)

    def _snapshot_state(self) -> dict:
        return {
            "status": self.status,
            "goal": self.goal,
            "step": len(self.history),
            "page": {
                "url": self._page.get("url"),
                "title": self._page.get("title"),
                "fingerprint": self._page.get("fingerprint"),
                "stats": self._page.get("stats"),
            },
            "decision": self._decision,
            "history": self.history,
            "elapsed_ms": self._elapsed(),
        }

    # -- canvas fallback ---------------------------------------------------
    def _canvas_click(self, instruction: str) -> dict:
        snap = self.browser.observe(screenshot=True)
        self._record("screenshot", {"for": "canvas_fallback"})
        if self.record_dir:
            (self.record_dir / f"canvas_{self._elapsed():06d}.jpg").write_bytes(snap["screenshot"])
        x1000, y1000 = ground(snap["screenshot"], instruction)
        vp = self.browser.page.viewport_size or {"width": 1366, "height": 900}
        x, y = to_pixels(x1000, y1000, vp["width"], vp["height"])
        self.browser.page.mouse.click(x, y)
        self._fallback_uses += 1
        return {"ok": True, "via": "canvas", "x": x, "y": y}

    # -- one decision cycle -------------------------------------------------
    def tick(self) -> dict:
        if self.started_at is None:
            self.started_at = time.perf_counter()
        if self.status in ("done", "blocked"):
            return self._snapshot_state()
        if len(self.history) >= self.max_steps:
            self.status = "blocked"
            self._record("blocked", {"reason": "step_budget"})
            return self._snapshot_state()

        if not self.browser.fresh(self._page):
            self._page = self.browser.observe()
        try:
            decision = choose(self.client, self._page, self.goal, self.history,
                              min_confidence=self.min_confidence)
        except JevError as e:
            self.status = "blocked"
            self._record("blocked", {"reason": f"jev_error: {e}"})
            return self._snapshot_state()

        self._decision = {k: v for k, v in decision.items() if k != "action"}
        if decision.get("action"):
            self._decision["target_label"] = decision["action"].get("_label")
        self._record("decision", self._decision)

        try:
            self._execute(decision)
        except StalePage:
            self._page = self.browser.observe()
            self._record("stale_page", {})
            return self._snapshot_state()

        self._page = self.browser.observe()
        self._detect_stall()
        return self._snapshot_state()

    def _execute(self, decision: dict):
        op = decision["operation"]
        action = decision.get("action")

        if op in ("DONE", "BLOCKED"):
            self._finish(op)
            return

        if op in ("SCROLL_UP", "SCROLL_DOWN"):
            self.browser.scroll_page("up" if op == "SCROLL_UP" else "down")
            self._log(op, None, {"ok": True})
            return
        if op == "WAIT":
            self.browser.wait(400)
            self._log(op, None, {"ok": True})
            return

        # Operations that need an element target. If Jev offered none,
        # the canvas fallback grounds it visually instead of giving up.
        if action is None:
            if self.canvas_fallback and op in ("CLICK", "CHECK", "FILL"):
                report = self._canvas_click(f"the control to click next for: {self.goal}")
                self._log(op, None, report)
                return
            self._log(op, None, {"ok": False, "reason": "no_compatible_target"})
            return

        text = None
        if op == "FILL":
            text = field_text(action.get("_label", ""), self.goal, self._page.get("text", ""))
        elif op == "SELECT":
            options = action.get("options") or []
            text = choose_option(options, self.goal) if options else None
        elif op == "UPLOAD" and not self.upload_file:
            self._log(op, action, {"ok": False, "reason": "no_upload_file_configured"})
            return

        if op == "DRAG":
            report = self.browser.act(action, self._page,
                                      drag_target=decision.get("drop_action"))
        elif op == "UPLOAD":
            report = self.browser.act(action, self._page, file_path=self.upload_file)
        else:
            report = self.browser.act(action, self._page, text=text)

        # Occluded or cross-origin frame: one visual retry before moving on.
        if not report.get("ok") and self.canvas_fallback and self._fallback_uses < 3:
            label = action.get("_label", "")
            try:
                report = self._canvas_click(f"{label} for: {self.goal}")
            except GroundingError as e:
                report = {"ok": False, "reason": f"canvas_failed: {e}"}

        self._log(op, action, report, text=text)

    def _log(self, op: str, action: dict | None, report: dict, text: str | None = None):
        entry = {
            "step": len(self.history) + 1,
            "operation": op,
            "target": action.get("_label") if action else None,
            "target_id": action.get("id") if action else None,
            "confidence": self._decision.get("confidence"),
            "latency_ms": self._decision.get("latency_ms"),
            "report": report,
            "text_typed": bool(text),
            "page_changed": None,  # filled after re-observe
            "url": self._page.get("url"),
            "elapsed_ms": self._elapsed(),
        }
        self.history.append(entry)
        self._record("action", entry)

    def _detect_stall(self):
        for h in self.history:
            if h["page_changed"] is None:
                h["page_changed"] = self._page.get("fingerprint") != self._last_fingerprint
        self._last_fingerprint = self._page.get("fingerprint")
        recent = self.history[-3:]
        if len(recent) == 3 and all(
            h["page_changed"] is False and h["operation"] != "WAIT" for h in recent
        ):
            self.status = "blocked"
            self._record("blocked", {"reason": "no_progress_3_steps"})

    _last_fingerprint: str | None = None

    def _finish(self, op: str):
        if op == "BLOCKED":
            self.status = "blocked"
            self._record("blocked", {"reason": "jev_blocked"})
            return
        # DONE is never trusted: verify independently.
        if self.verify:
            page = self.browser.observe()
            verdict = verify_done(self.client, self.goal, page.get("text", ""))
            self._record("verify", verdict)
            if not verdict["achieved"]:
                self._verify_failures += 1
                if self._verify_failures >= 2:
                    self.status = "blocked"
                    self._record("blocked", {"reason": "verify_failed_twice"})
                return
        self.status = "done"
        self._record("done", {})

    def run(self):
        while self.status not in ("done", "blocked"):
            yield self.tick()
        yield self._snapshot_state()
