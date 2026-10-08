"""Jev decision client + small text helper.

One TypeSafe/OpenRouter request per decision cycle carries the operation
question plus speculative target questions. Only the target head matching
the chosen operation can execute, so N decisions cost one round trip.
"""

from __future__ import annotations

import os
import time

import httpx

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
OPENROUTER_URL = "https://openrouter.ai/api/alpha/decisions"

OPERATIONS = [
    "CLICK",
    "FILL",
    "SELECT",
    "CHECK",
    "UPLOAD",
    "DRAG",
    "SCROLL_UP",
    "SCROLL_DOWN",
    "WAIT",
    "DONE",
    "BLOCKED",
]

OP_DESCRIPTIONS = {
    "CLICK": "Click a button, link, or other clickable control.",
    "FILL": "Type text into a text field, search box, or editable area.",
    "SELECT": "Choose an option from a dropdown/combobox.",
    "CHECK": "Toggle a checkbox, radio button, or switch.",
    "UPLOAD": "Attach a file via a file-upload control.",
    "DRAG": "Drag one element onto another (needs drag source and drop target).",
    "SCROLL_UP": "Scroll the page up to reveal more content.",
    "SCROLL_DOWN": "Scroll the page down to reveal more content.",
    "WAIT": "Wait briefly for loading or animations to settle.",
    "DONE": "The goal is achieved; stop.",
    "BLOCKED": "The goal cannot be achieved from here; stop.",
}

# max options per target head (Jev choice supports up to 255)
HEAD_LIMIT = 150


class JevError(Exception):
    pass


class JevClient:
    def __init__(
        self,
        provider: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ):
        provider = (provider or os.getenv("JEV_PROVIDER") or "").lower()
        if not provider:
            provider = "typesafe" if os.getenv("TYPESAFE_API_KEY") else "openrouter"
        if provider == "typesafe":
            self.url = TYPESAFE_URL
            self.api_key = api_key or os.getenv("TYPESAFE_API_KEY", "")
            self.model = model or os.getenv("JEV_MODEL") or "jev-latest"
        elif provider == "openrouter":
            self.url = OPENROUTER_URL
            self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
            self.model = model or os.getenv("JEV_MODEL") or "typesafe/jev-1.13"
        else:
            raise JevError(f"unknown Jev provider: {provider}")
        if not self.api_key:
            raise JevError(f"missing API key for provider '{provider}'")
        self.provider = provider
        self._http = httpx.Client(timeout=timeout)

    def decide(self, state: str, questions: dict) -> dict:
        """Ask all questions in one request. Returns {answers, usage, latency_ms}."""
        body = {"state": state, "model": self.model, "questions": questions}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/saloni-garg/jev-max"
            headers["X-Title"] = "jev-max"
        started = time.perf_counter()
        try:
            r = self._http.post(self.url, json=body, headers=headers)
        except httpx.RequestError as e:
            raise JevError(f"Jev request failed: {e}")
        ms = round((time.perf_counter() - started) * 1000)
        if r.status_code == 401:
            raise JevError("Jev authentication failed (401): check your API key")
        if r.status_code == 429:
            raise JevError("Jev rate limited (429): back off and retry")
        if r.status_code >= 400:
            raise JevError(f"Jev error {r.status_code}: {r.text[:300]}")
        data = r.json()
        return {"answers": data.get("answers", {}), "usage": data.get("usage", {}), "latency_ms": ms}


def render_table(actions: list[dict]) -> tuple[str, dict[str, list[dict]]]:
    """Render the element table as indexed text; group compatible targets per head."""
    heads: dict[str, list[dict]] = {
        "click_target": [], "fill_target": [], "select_target": [],
        "upload_target": [], "drag_source": [], "drag_target": [],
    }
    for a in actions:
        if a.get("disabled"):
            continue
        label = f"[{a['id']}] {a['role']} {a['name']}".strip()
        if a.get("value"):
            label += f" · {a['value']}"
        if a.get("options"):
            label += f" (options: {', '.join(a['options'][:8])})"
        flags = []
        if a.get("offscreen"):
            flags.append("offscreen")
        if a.get("inShadow"):
            flags.append("shadow")
        if a.get("inFrame"):
            flags.append("iframe")
        if a.get("crossOrigin"):
            flags.append("cross-origin")
        if flags:
            label += f" ({', '.join(flags)})"
        a["_label"] = label
        kind = a["kind"]
        if kind in ("click", "check", "frame"):
            heads["click_target"].append(a)
        elif kind == "fill":
            heads["fill_target"].append(a)
        elif kind == "select":
            heads["select_target"].append(a)
        elif kind == "upload":
            heads["upload_target"].append(a)
        if kind in ("click", "check"):
            heads["drag_source"].append(a)
            heads["drag_target"].append(a)

    lines = []
    shown = set()
    for head_actions in heads.values():
        # visible, in-viewport first; keep each head within the option budget
        ordered = sorted(head_actions, key=lambda a: (a.get("offscreen", False), a["id"]))
        for a in ordered[:HEAD_LIMIT]:
            if a["id"] not in shown:
                lines.append(a["_label"])
                shown.add(a["id"])
    lines.sort(key=lambda l: int(l.split("]")[0][1:]))
    return "\n".join(lines), heads


def _criteria(actions: list[dict]) -> dict:
    ordered = sorted(actions, key=lambda a: (a.get("offscreen", False), a["id"]))
    return {str(a["id"]): a["_label"] for a in ordered[:HEAD_LIMIT]}


def build_questions(table: str, heads: dict, goal: str, history: list[dict]) -> dict:
    hist = "\n".join(
        f"{h['step']}. {h['operation']} [{h.get('target')}] {'(page changed)' if h.get('page_changed') else ''}"
        for h in history[-8:]
    )
    state = (
        f"Goal: {goal}\n\n"
        f"Page controls:\n{table}\n\n"
        f"Actions so far:\n{hist or '(none)'}\n"
    )
    questions = {
        "operation": {
            "type": "choice",
            "instructions": (
                "Choose the single best next browser operation for the goal. "
                "Prefer the most direct control. Choose DONE only when the goal is "
                "visibly achieved, BLOCKED only when no control can advance it."
            ),
            "criteria": OP_DESCRIPTIONS,
        }
    }
    head_instructions = {
        "click_target": "Which control should be clicked?",
        "fill_target": "Which field should receive typed text?",
        "select_target": "Which dropdown should change?",
        "upload_target": "Which control accepts the file upload?",
        "drag_source": "Which element should be dragged?",
        "drag_target": "Which element should receive the drop?",
    }
    for head, actions in heads.items():
        if not actions:
            continue
        questions[head] = {
            "type": "choice",
            "instructions": head_instructions[head] + " Answer with the control's index.",
            "criteria": _criteria(actions),
        }
    return {"state": state, "questions": questions}


def choose(client: JevClient, snap: dict, goal: str, history: list[dict],
           min_confidence: float = 0.0) -> dict:
    """One decision cycle. Returns the resolved operation + target action."""
    table, heads = render_table(snap["actions"])
    payload = build_questions(table, heads, goal, history)
    res = client.decide(payload["state"], payload["questions"])
    answers = res["answers"]

    op_answer = answers.get("operation", {})
    operation = op_answer.get("choice", "WAIT")
    confidence = op_answer.get("confidence", 0.0)
    probabilities = op_answer.get("probabilities", {})

    target = None
    head_for_op = {
        "CLICK": "click_target", "CHECK": "click_target",
        "FILL": "fill_target", "SELECT": "select_target",
        "UPLOAD": "upload_target",
    }.get(operation)
    if head_for_op and head_for_op in answers:
        target = answers[head_for_op].get("choice")

    drag_source = drag_target = None
    if operation == "DRAG":
        drag_source = answers.get("drag_source", {}).get("choice")
        drag_target = answers.get("drag_target", {}).get("choice")

    by_id = {str(a["id"]): a for a in snap["actions"]}
    decision = {
        "operation": operation,
        "target": target,
        "drag_source": drag_source,
        "drag_target": drag_target,
        "action": by_id.get(str(target)),
        "confidence": confidence,
        "probabilities": probabilities,
        "below_confidence": confidence < min_confidence,
        "latency_ms": res["latency_ms"],
        "usage": res["usage"],
    }
    if operation == "DRAG":
        decision["action"] = by_id.get(str(drag_source))
        decision["drop_action"] = by_id.get(str(drag_target))
    return decision


# -- small text helper (only used for FILL) --------------------------------


def field_text(field_label: str, goal: str, page_text: str,
               api_key: str | None = None, model: str | None = None,
               base_url: str | None = None, timeout: float = 30.0) -> str:
    """Generate the exact string to type into a field. Returns plain text."""
    api_key = api_key or os.getenv("TEXT_MODEL_API_KEY", "")
    if not api_key:
        raise JevError("TEXT_MODEL_API_KEY is required to generate field text")
    model = model or os.getenv("TEXT_MODEL_NAME", "inception/mercury-2.5")
    base_url = base_url or os.getenv("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    prompt = (
        "You fill one web form field. Reply with ONLY the exact text to type, "
        "no quotes, no explanation.\n"
        f"Goal: {goal}\nField: {field_label}\n"
        f"Page context: {page_text[:800]}\n"
        "Text to type:"
    )
    r = httpx.post(
        base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.2, "max_tokens": 200},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip().strip('"')


def choose_option(options: list[str], goal: str,
                  api_key: str | None = None, model: str | None = None,
                  base_url: str | None = None, timeout: float = 30.0) -> str:
    """Pick the dropdown option label that best serves the goal."""
    api_key = api_key or os.getenv("TEXT_MODEL_API_KEY", "")
    if not api_key:
        raise JevError("TEXT_MODEL_API_KEY is required to choose a dropdown option")
    model = model or os.getenv("TEXT_MODEL_NAME", "inception/mercury-2.5")
    base_url = base_url or os.getenv("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    numbered = "\n".join(f"{i}. {o}" for i, o in enumerate(options))
    prompt = (
        "Pick one dropdown option. Reply with ONLY the exact option text, nothing else.\n"
        f"Goal: {goal}\nOptions:\n{numbered}\nChoice:"
    )
    r = httpx.post(
        base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.1, "max_tokens": 60},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip().strip('"')


# -- verifier (independent outcome check) ------------------------------------

def verify_done(client: JevClient, goal: str, page_text: str, screenshot_note: str = "") -> dict:
    """Ask Jev whether the goal is achieved. Never trusts the actor loop."""
    res = client.decide(
        f"Goal: {goal}\n\nFinal page text:\n{page_text[:2000]}\n{screenshot_note}",
        {"goal_achieved": {
            "type": "noul",
            "instructions": "Is the goal fully achieved on this page?",
            "criteria": {
                "true": "The page visibly shows the goal outcome.",
                "false": "The outcome is missing, partial, or unclear.",
            },
        }},
    )
    ans = res["answers"].get("goal_achieved", {})
    return {"achieved": ans.get("noul", 0.0) >= 0.7, "score": ans.get("noul", 0.0)}
