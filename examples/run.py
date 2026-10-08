"""Example: run jev-max on any URL + goal. Needs API keys in .env."""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv

from jev_max import Agent


def main():
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--goal", required=True)
    p.add_argument("--max-steps", type=int, default=25)
    p.add_argument("--headful", action="store_true")
    a = p.parse_args()

    with Agent(a.url, a.goal, max_steps=a.max_steps,
               headless=not a.headful) as agent:
        for state in agent.run():
            d = state.get("decision") or {}
            print(f"[{state['step']:2d}] {d.get('operation', '-'):<10} "
                  f"{(d.get('target_label') or '')[:60]}")
        print(f"\n{state['status'].upper()} -> {state['page']['url']}")


if __name__ == "__main__":
    main()
