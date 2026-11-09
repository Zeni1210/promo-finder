"""Score the tool's promos against hand-labelled editor picks (eval/gold_moments.csv).

A gold moment counts as *found* by a promo when they share an episode and share
at least 10 s of footage, or at least half of the shorter of the two spans
(10 s of the same footage = the same moment, even if the cut points differ). Gold is not exhaustive, so precision here is a lower bound - unmatched
top picks are listed for manual review rather than counted as wrong.
"""
from __future__ import annotations

import csv

from .parse import to_sec


def load_gold(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["episode"] = int(r["episode"])
        r["s"], r["e"] = to_sec(r["start"]), to_sec(r["end"])
    return rows


def matches(promo, g) -> bool:
    segs = [s for s in promo.segments if s.episode == g["episode"]]
    if not segs:
        return False
    ov = sum(max(0.0, min(s.end, g["e"]) - max(s.start, g["s"])) for s in segs)
    plen = sum(s.dur for s in segs)
    return ov >= 10.0 or (ov >= 0.5 * min(plen, g["e"] - g["s"]) and ov > 3.0)


def evaluate(promos, gold, ks=(10, 15, 30)):
    out = {"n_gold": len(gold), "n_gold_A": sum(g["tier"] == "A" for g in gold), "n_promos": len(promos)}
    found_by = {}
    for g in gold:
        hits = [p.rank for p in promos if matches(p, g)]
        found_by[g["id"]] = min(hits) if hits else None
    out["recall_all"] = round(sum(v is not None for v in found_by.values()) / len(gold), 3)
    aid = [g["id"] for g in gold if g["tier"] == "A"]
    out["recall_A_all"] = round(sum(found_by[i] is not None for i in aid) / max(len(aid), 1), 3)
    for k in ks:
        out[f"recall@{k}"] = round(sum(v is not None and v <= k for v in found_by.values()) / len(gold), 3)
        out[f"recall_A@{k}"] = round(sum(found_by[i] is not None and found_by[i] <= k for i in aid)
                                     / max(len(aid), 1), 3)
        top = [p for p in promos if p.rank <= k]
        out[f"precision@{k}"] = round(sum(any(matches(p, g) for g in gold) for p in top) / max(len(top), 1), 3)
    out["found_by_rank"] = found_by
    out["qa_pass_rate"] = round(sum(p.qa["pass"] for p in promos) / max(len(promos), 1), 3)
    return out


def split_eval(promos, gold, cut=25):
    """Report separately for episodes <=cut (where rules were tuned) and >cut (held out)."""
    res = {}
    for name, keep in (("tuned_eps_1-%d" % cut, lambda e: e <= cut), ("heldout_eps_%d+" % (cut + 1), lambda e: e > cut)):
        g = [x for x in gold if keep(x["episode"])]
        p = [x for x in promos if keep(x.segments[0].episode)]
        found = sum(any(matches(q, x) for q in p) for x in g)
        a = [x for x in g if x["tier"] == "A"]
        found_a = sum(any(matches(q, x) for q in p) for x in a)
        res[name] = {"gold": len(g), "found": found, "recall": round(found / max(len(g), 1), 3),
                     "gold_A": len(a), "found_A": found_a, "recall_A": round(found_a / max(len(a), 1), 3)}
    return res
