"""Run benchmark tasks, evaluate checks, write results + traces."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..agent import Agent

TASKS_FILE = Path(__file__).with_name("tasks.yaml")
FIXTURE_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures"


def load_tasks(task_ids: list[str] | None = None) -> list[dict]:
    data = yaml.safe_load(TASKS_FILE.read_text())
    tasks = data["tasks"]
    for t in tasks:
        t["url"] = t["url"].replace("FIXTURE_DIR", str(FIXTURE_DIR))
    if task_ids:
        tasks = [t for t in tasks if t["id"] in task_ids]
    return tasks


def evaluate(task: dict, final: dict) -> dict:
    check = task["check"]
    ctype, value = check["type"], check["value"]
    page = final["page"]
    if ctype == "text_contains":
        text = final.get("_final_text", "")
        passed = value.lower() in text.lower()
    elif ctype == "title_contains":
        passed = value.lower() in (page.get("title") or "").lower()
    elif ctype == "url_contains":
        passed = value.lower() in (page.get("url") or "").lower()
    else:
        passed = False
    return {"check": check, "passed": passed}


def run_benchmark(task_ids: list[str] | None = None, out_dir: str | Path = "benchmark/results",
                  max_steps: int = 25, provider: str | None = None,
                  headless: bool = True) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    results = {"run_id": run_id, "tasks": []}

    for task in load_tasks(task_ids):
        trace_dir = out_dir / "traces" / f"{run_id}-{task['id']}"
        print(f"\n== {task['id']}: {task['goal'][:70]}")
        started = time.perf_counter()
        entry: dict = {"id": task["id"], "goal": task["goal"]}
        try:
            with Agent(task["url"], task["goal"], max_steps=max_steps,
                       provider=provider, headless=headless,
                       record_dir=trace_dir) as agent:
                final = None
                for state in agent.run():
                    final = state
                    d = state.get("decision") or {}
                    print(f"  step {state['step']:2d} {d.get('operation', '-'):<10} "
                          f"{(d.get('target_label') or '')[:50]}")
                final_text = agent.browser.page.evaluate("document.body.innerText || ''")
                final["_final_text"] = final_text
                verdict = evaluate(task, final)
                entry.update(
                    status=final["status"],
                    steps=len(final["history"]),
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                    jev_calls=len([h for h in final["history"] if h.get("confidence") is not None]),
                    **verdict,
                )
        except Exception as e:  # record failures, don't abort the suite  # noqa: BLE001
            entry.update(status="error", error=str(e)[:200], passed=False,
                         elapsed_ms=round((time.perf_counter() - started) * 1000))
        entry["trace"] = str(trace_dir)
        results["tasks"].append(entry)
        mark = "PASS" if entry.get("passed") else "FAIL"
        print(f"  -> {mark} ({entry.get('status')}, {entry.get('elapsed_ms')}ms)")

    passed = sum(1 for t in results["tasks"] if t.get("passed"))
    results["summary"] = {"passed": passed, "total": len(results["tasks"])}
    (out_dir / f"results-{run_id}.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"\n{passed}/{len(results['tasks'])} passed. Results in {out_dir}/results-{run_id}.json")
    return results
