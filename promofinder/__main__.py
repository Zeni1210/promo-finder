"""CLI.

    python -m promofinder run --input "<folder of episode .txt files>" --out outputs/
    python -m promofinder run --input ... --out ... --provider gemini            # + LLM editor pass
    python -m promofinder run --input ... --out ... --gold eval/gold_moments.csv  # + score vs editor picks
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import sys
import time

from .pipeline import build
from .llm import LLM, LLMError
from .report import write_all
from . import evaluate as E

# public list prices (USD per 1M tokens, in/out), used only to print a cost estimate
# (list prices I knew of when writing this; check current pricing before quoting)
PRICES = {"gemini-2.0-flash": (0.10, 0.40), "gemini-2.5-flash": (0.30, 2.50), "gpt-4o-mini": (0.15, 0.60),
          "claude-sonnet-4-5": (3.00, 15.00)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="promofinder", description="Find promo clips in a micro-drama screenplay.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="find, rank and QA promos for one show")
    r.add_argument("--input", required=True, help="folder with one timestamped screenplay .txt per episode")
    r.add_argument("--out", required=True, help="output folder")
    r.add_argument("--title", help="show title (defaults to the folder name)")
    r.add_argument("--provider", default=os.environ.get("PROMOFINDER_PROVIDER", "none"),
                   choices=["none", "gemini", "openai", "anthropic", "ollama"])
    r.add_argument("--model", default=os.environ.get("PROMOFINDER_MODEL"))
    r.add_argument("--base-url", default=os.environ.get("PROMOFINDER_BASE_URL"),
                   help="OpenAI-compatible endpoint (Groq, OpenRouter, Together, Ollama, your own gateway)")
    r.add_argument("--api-key", default=None, help="else read from GEMINI_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY")
    r.add_argument("--rpm", type=float, default=10, help="max model calls per minute (free tiers are rate-limited)")
    r.add_argument("--per-episode", type=int, default=3, help="max promos anchored in any one episode")
    r.add_argument("--batch", type=int, default=5,
                   help="episodes reviewed per model call (5 -> ~11 calls for 50 episodes; 1 = most careful)")
    r.add_argument("--top", type=int, default=15, help="size of the post-first shortlist")
    r.add_argument("--gold", help="optional CSV of editor-picked moments to score against")
    r.add_argument("--cache", default=".cache/llm")
    a = ap.parse_args(argv)

    lines = []

    def log(msg):
        print(msg, flush=True)
        lines.append(msg)

    t0 = time.time()
    try:
        llm = LLM(a.provider, a.model, a.base_url, a.api_key, rpm=a.rpm, cache_dir=a.cache, log=log)
    except LLMError as e:
        sys.exit(f"error: {e}")
    res = build(a.input, title=a.title, per_episode=a.per_episode, llm=llm, batch=a.batch, log=log)

    ev = None
    if a.gold:
        gold = E.load_gold(a.gold)
        ev = E.evaluate(res["promos"], gold)
        ev["split"] = E.split_eval(res["promos"], gold)
        ev["gold_rows"] = gold
        ev["unmatched_top"] = [f"{c.rank}. {c.pid} {', '.join(c.desc['cuts'])} - {c.desc['hook'][:70]}"
                               for c in res["promos"][:30] if not any(E.matches(c, g) for g in gold)]
        log(f"[eval] recall {ev['recall_all']:.0%} of {ev['n_gold']} editor moments "
            f"(tier A {ev['recall_A_all']:.0%}); tier-A in top 15: {ev['recall_A@15']:.0%}; "
            f"precision@15 {ev['precision@15']:.0%}")

    cost = None
    if llm.enabled:
        pin, pout = PRICES.get(llm.model, (None, None))
        if pin is not None:
            cost = round(llm.tokens_in / 1e6 * pin + llm.tokens_out / 1e6 * pout, 4)
        log(f"[llm] {llm.calls} calls ({llm.cached} cached), {llm.tokens_in} in / {llm.tokens_out} out tokens"
            + (f", ~${cost} at list price" if cost is not None else ""))
    run_info = {"when": dt.datetime.now().isoformat(timespec="seconds"), "input": os.path.abspath(a.input),
                "mode": "rules + LLM (" + llm.describe() + ")" if llm.enabled else "rules only (no model calls)",
                "python": platform.python_version(), "seconds": round(time.time() - t0, 2),
                "candidates_considered": res["candidates_considered"], "promos": len(res["promos"]),
                "llm_calls": llm.calls, "llm_cached": llm.cached, "tokens_in": llm.tokens_in,
                "tokens_out": llm.tokens_out, "est_cost_usd": cost if llm.enabled else 0.0,
                "command": "python -m promofinder " + " ".join(sys.argv[1:] if argv is None else argv)}
    write_all(res, a.out, run_info, lines, ev, top=a.top)
    log(f"[out] wrote promos.json, promos.csv, edit_decision_list.csv, shortlist.md, report.html, "
        f"qa_report.md, run_log.txt -> {a.out}")


if __name__ == "__main__":
    main()
