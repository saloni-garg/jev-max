# jev-max

**The Jev browser agent that handles the real web.**

Jev (TypeSafe's System One decision model) picks browser operations at ~$0.04 per million input tokens. Existing Jev agents are fast but stop at an MVP action space: no shadow DOM, no iframes, no canvas, no uploads, one tab, one agent host. jev-max keeps the speed and fills the gaps.

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
| Independent DONE verification | Yes | Yes (Codex) | **Yes** (Jev noul re-check) |
| Agent hosts | Standalone Python | Codex only | **Any MCP host** (Claude Code, Cursor, Codex, ...) + standalone |
| Browser | Browser Harness (Chrome remote debug) | Codex Computer Use | **Playwright** (Chromium/Firefox/WebKit) |
| Benchmark | 1 task, 3 repeats | n/a | **Multi-task suite with traces** |

Credit where due: the one-request speculative action space is inspired by jev-ultrafast, and the never-trust-DONE verification split by jev-browser-use. Both are MIT and linked above.

## Try it

```bash
git clone https://github.com/saloni-garg/jev-max.git
cd jev-max
python -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium
cp .env.example .env   # add TYPESAFE_API_KEY or OPENROUTER_API_KEY + TEXT_MODEL_API_KEY
```

No API key needed to see the piercing snapshot:

```bash
jev-max snapshot --url https://example.com
```

Run a task (needs keys):

```bash
jev-max run --url https://en.wikipedia.org/wiki/Main_Page \
  --goal "Find and open the Wikipedia article about Godel's incompleteness theorems." \
  --record-dir traces/wiki
```

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
- **Piercing snapshot.** `snapshot.js` walks shadow roots and same-origin iframes into a single indexed table. Cross-origin iframes are recorded as regions so the canvas fallback can still work them.
- **Two-tier text.** A small LLM writes text only for `FILL` and picks dropdown options only for `SELECT`. Jev never generates free text.
- **Canvas fallback.** When the table has no usable target (canvas UI, covered control, cross-origin region), a vision model grounds the instruction to coordinates and the real mouse clicks.
- **Occlusion-checked clicks.** Shadow-aware, frame-aware `elementFromPoint` verification before every click. Covered controls are reported, never blindly clicked.
- **Model output never becomes selectors, coordinates, or JS.** Targets resolve from observed nodes only.

## Use the library

```python
from jev_max import Agent

with Agent("https://www.google.com/travel/flights?hl=en",
           "Find one-way flights from Zurich to London on September 20, 2026. "
           "Stop when matching flight options are visible.") as agent:
    for state in agent.run():
        print(state["step"], state["status"], state["decision"]["operation"])
```

## MCP server (any agent host)

```bash
jev-max mcp
```

Tools: `jev_start(url, goal, max_steps)` -> `jev_tick(session_id)` until `done`/`blocked` -> `jev_close(session_id)`. Works in Claude Code, Cursor, Windsurf, Codex, or anything speaking MCP. Set `JEV_HEADLESS=0` to watch it work.

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

Jev 1.13 is $0.042/M input tokens with free output (TypeSafe and OpenRouter). A typical 10-step task costs a fraction of a cent in decision tokens; text generation uses your small model of choice.

MIT licensed. Not affiliated with TypeSafe, OpenRouter, or browser-use.
