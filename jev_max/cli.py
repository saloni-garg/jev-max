"""jev-max CLI: run tasks, print snapshots, serve MCP, run benchmarks."""

from __future__ import annotations

import argparse
import json
import sys

from dotenv import load_dotenv


def _cmd_run(a):
    from .agent import Agent
    with Agent(
        a.url, a.goal,
        provider=a.provider, headless=not a.headful, max_steps=a.max_steps,
        record_dir=a.record_dir, upload_file=a.upload_file,
        verify=not a.no_verify, min_confidence=a.min_confidence,
    ) as agent:
        final = None
        for state in agent.run():
            final = state
            d = state.get("decision") or {}
            op = d.get("operation", "-")
            tgt = (d.get("target_label") or "")[:60]
            print(f"[{state['step']:2d}] {op:<10} {tgt} "
                  f"(conf {d.get('confidence', 0):.2f})", flush=True)
        print(f"\n{final['status'].upper()} in {final['elapsed_ms']}ms, "
              f"{len(final['history'])} steps -> {final['page']['url']}")
        if a.json:
            print(json.dumps(final, default=str))
        return 0 if final["status"] == "done" else 1


def _cmd_snapshot(a):
    from .browser import Browser
    b = Browser(headless=not a.headful)
    try:
        snap = b.goto(a.url)
        print(f"# {snap['title']}\n# {snap['url']}")
        s = snap["stats"]
        print(f"# controls={s['controls']} shadow_pierced={s['shadowPierced']} "
              f"frames_pierced={s['framePierced']} cross_origin={s['crossOriginFrames']}")
        for act in snap["actions"]:
            flags = []
            if act.get("offscreen"):
                flags.append("offscreen")
            if act.get("inShadow"):
                flags.append("shadow")
            if act.get("inFrame"):
                flags.append("iframe")
            if act.get("crossOrigin"):
                flags.append("cross-origin")
            flag = f" ({', '.join(flags)})" if flags else ""
            extra = f" options={act['options'][:5]}" if act.get("options") else ""
            print(f"[{act['id']}] {act['kind']:<6} {act['role']:<10} {act['name']}{flag}{extra}")
    finally:
        b.close()


def _cmd_mcp(_a):
    from .mcp_server import main
    main()


def _cmd_benchmark(a):
    from .benchmark import run_benchmark
    run_benchmark(task_ids=a.tasks or None, out_dir=a.out_dir,
                  max_steps=a.max_steps, provider=a.provider,
                  headless=not a.headful)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jev-max", description="The Jev browser agent that handles the real web.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Run a browser task from a goal.")
    r.add_argument("--url", required=True)
    r.add_argument("--goal", required=True)
    r.add_argument("--provider", choices=["typesafe", "openrouter"])
    r.add_argument("--max-steps", type=int, default=25)
    r.add_argument("--min-confidence", type=float, default=0.0)
    r.add_argument("--headful", action="store_true")
    r.add_argument("--record-dir")
    r.add_argument("--upload-file")
    r.add_argument("--no-verify", action="store_true")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=_cmd_run)

    s = sub.add_parser("snapshot", help="Print the pierced element table for a URL (no API key needed).")
    s.add_argument("--url", required=True)
    s.add_argument("--headful", action="store_true")
    s.set_defaults(fn=_cmd_snapshot)

    m = sub.add_parser("mcp", help="Serve the MCP server on stdio for any agent host.")
    m.set_defaults(fn=_cmd_mcp)

    b = sub.add_parser("benchmark", help="Run the reproducible task benchmark.")
    b.add_argument("--tasks", nargs="*")
    b.add_argument("--out-dir", default="benchmark/results")
    b.add_argument("--max-steps", type=int, default=25)
    b.add_argument("--provider", choices=["typesafe", "openrouter"])
    b.add_argument("--headful", action="store_true")
    b.set_defaults(fn=_cmd_benchmark)
    return p


def main(argv=None):
    load_dotenv()
    args = build_parser().parse_args(argv)
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
