# jev-max

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](pyproject.toml)
[![Jev](https://img.shields.io/badge/powered_by-TypeSafe_Jev-orange.svg)](https://typesafe.ai)

**The Jev browser agent that breaks the three walls.**

Jev (TypeSafe's System One model) made browser agents ~10x faster and ~100x cheaper by replacing screenshots with typed decisions: one cheap model call picks the operation and the element. But every Jev agent ships the same MVP limits. Pages built with **shadow DOM**, **iframes**, or **canvas** are invisible to them.

jev-max keeps the speed and knocks the walls down:

- **Piercing snapshot** — walks open shadow roots and same-origin iframes into one indexed element table. Cross-origin frames are recorded as regions instead of silently dropped.
- **Canvas fallback** — when the table has no usable target, a vision model grounds the instruction to coordinates and the real mouse clicks.
- **Full action space** — uploads, drag-and-drop, multi-tab, nested scroll, native selects with option lists.
- **Occlusion-checked clicks** — shadow- and frame-aware hit testing before every click. Covered controls are reported, never blindly clicked.
- **One Jev request per cycle** — operation plus speculative target heads share a single call (TypeSafe or OpenRouter).
- **Two-tier text** — a small LLM writes text only for `FILL`/`SELECT`. Jev never generates free text.
- **Never-trust-DONE verification** — an independent Jev check confirms the outcome before stopping.
- **Any agent host** — standalone Python library plus an MCP server (Claude Code, Cursor, Windsurf, Codex).

## Quick start

```bash
pip install jev-max
playwright install chromium
cp .env.example .env   # add TYPESAFE_API_KEY or OPENROUTER_API_KEY + TEXT_MODEL_API_KEY
```

See the piercing snapshot with no API key:

```bash
jev-max snapshot --url https://example.com
```

Run a task:

```bash
jev-max run --url https://en.wikipedia.org/wiki/Main_Page \
  --goal "Find and open the Wikipedia article about Godel's incompleteness theorems." \
  --record-dir traces/wiki
```

```python
from jev_max import Agent

with Agent("https://www.google.com/travel/flights?hl=en",
           "Find one-way flights from Zurich to London on September 20, 2026. "
           "Stop when matching flight options are visible.") as agent:
    for state in agent.run():
        print(state["step"], state["status"], state["decision"]["operation"])
```

## How it compares

| | [jev-ultrafast](https://github.com/browser-use/jev-ultrafast) | [jev-browser-use](https://github.com/wy-coliney/jev-browser-use) | **jev-max** |
|---|---|---|---|
| Decision model | Jev, 1 request / cycle | Jev for clicks, Codex for the rest | Jev, 1 request / cycle (speculative target heads) |
| Shadow DOM piercing | No | No | **Yes** |
| Same-origin iframe piercing | No | No | **Yes** |
| Cross-origin iframe regions | No | No | Marked + canvas fallback |
| Canvas / visual fallback | No | No | **Yes** (vision grounding -> real mouse) |
| File uploads | No | No | **Yes** |
| Drag-and-drop | No | No | **Yes** |
| Multi-tab | No | via Codex runtime | **Yes** (popups auto-registered) |
| Nested scroll containers | No | No | **Yes** |
| Click occlusion checks | Yes | No | **Yes** (shadow- and frame-aware) |
| Independent DONE verification | Yes | Yes (Codex) | **Yes** (Jev re-check) |
| Agent hosts | Standalone Python | Codex only | **Any MCP host** + standalone |
| Browser | Browser Harness (Chrome remote debug) | Codex Computer Use | **Playwright** (Chromium/Firefox/WebKit) |
| Benchmark | 1 task, 3 repeats | n/a | **Multi-task suite with traces** |

Credit where due: the one-request speculative action space is inspired by jev-ultrafast, and the never-trust-DONE split by jev-browser-use. Both are MIT and linked above.

## How it works

```
page -> snapshot.js -> indexed element table (shadow + iframes pierced)
                                          |
                    one Jev request: operation + speculative targets
                                          |
              CLICK/FILL/SELECT/CHECK/UPLOAD/DRAG/SCROLL/WAIT/DONE/BLOCKED
                                          |
                    no usable target? -> canvas fallback (vision -> coordinates)
                                          |
                    DONE? -> independent Jev verification, never trusted blindly
```

- **One request per decision cycle.** The operation question and every target head share one Jev call. Only the head matching the chosen operation can execute.
- **Piercing snapshot.** `snapshot.js` walks shadow roots and same-origin iframes into a single indexed table, with per-control role, name, value, and bounding box.
- **Model output never becomes selectors, coordinates, or JS.** Targets resolve from observed nodes only.

## MCP server (any agent host)

```bash
jev-max mcp
```

Tools: `jev_start(url, goal, max_steps)` -> `jev_tick(session_id)` until `done`/`blocked` -> `jev_close(session_id)`. Set `JEV_HEADLESS=0` to watch it work.

## Benchmark

```bash
jev-max benchmark
```

Runs `jev_max/benchmark/tasks.yaml`: shadow-DOM form fill, pierced-iframe click, plus live tasks. Each run writes traces and a `results-<timestamp>.json` with pass/fail. Add tasks with `check: {type: text_contains|title_contains|url_contains, value: ...}`.

## Project layout

| File | Job |
|---|---|
| `jev_max/snapshot.js` | Piercing DOM snapshot, indexed controls, freshness fingerprint |
| `jev_max/browser.py` | Playwright driver: tabs, uploads, drag-drop, occlusion checks |
| `jev_max/model.py` | Jev client (TypeSafe + OpenRouter), text helper, verifier |
| `jev_max/canvas.py` | Vision coordinate grounding fallback |
| `jev_max/agent.py` | The observe-decide-act loop |
| `jev_max/mcp_server.py` | MCP server for any agent host |
| `jev_max/benchmark/` | Reproducible task suite |
| `jev_max/cli.py` | `run` / `snapshot` / `mcp` / `benchmark` commands |

## Cost

Jev is $0.042 per million input tokens with free output (TypeSafe and OpenRouter). A typical 10-step task costs a fraction of a cent in decision tokens; text generation uses your small model of choice.

MIT licensed. Not affiliated with TypeSafe, OpenRouter, or browser-use.
