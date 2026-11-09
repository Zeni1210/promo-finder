"""End-to-end: screenplay folder -> ranked promos (rules), optionally refined by an LLM."""
from __future__ import annotations

import os
import re
import time
from collections import Counter, defaultdict

from .parse import load_show
from . import signals as S
from .candidates import (EpisodeIndex, single_windows, stitched_windows, cross_windows,
                         montage_windows, nms, _score, Candidate)
from .qa import check
from .describe import describe, post_reason


def build(folder: str, title: str | None = None, per_episode: int = 3, llm=None, batch: int = 5, log=print):
    t0 = time.time()
    eps = load_show(folder)
    if not eps:
        raise SystemExit(f"no .txt episodes found in {folder}")
    title = title or default_title(folder)
    premise = S.premise_terms(title, [e.logline for e in eps], names=_proper_names(eps))
    idx = {e.number: EpisodeIndex(e, premise) for e in eps}
    n = len(eps)
    log(f"[parse] {n} episodes, {sum(len(e.beats) for e in eps)} beats, "
        f"{sum(e.duration for e in eps)/60:.1f} min of screen time")
    log(f"[premise] {sorted(premise)}")

    all_c: list[Candidate] = []
    numbers = sorted(idx)
    for k, num in enumerate(numbers):
        ix = idx[num]
        c1 = single_windows(ix)
        c2 = stitched_windows(ix)
        c3 = cross_windows(ix, idx[numbers[k + 1]]) if k + 1 < len(numbers) else []
        for c in c1 + c2 + c3:
            _score(idx, c, len(premise), n)
        all_c.extend(c1 + c2 + c3)
    log(f"[candidates] {len(all_c)} raw windows "
        f"({sum(c.kind=='single' for c in all_c)} single, {sum(c.kind=='stitched' for c in all_c)} stitched, "
        f"{sum(c.kind=='cross_episode' for c in all_c)} cross-episode)")

    use_llm = llm is not None and llm.enabled
    picked = nms(all_c, thr=0.30, per_key_limit=6 if use_llm else per_episode)
    picked = [c for c in picked if c.score > 0]
    for c in picked:
        c.qa = check(idx, c)

    judge_log = []
    if use_llm:
        from .judge import judge_episode
        groups = defaultdict(list)
        for c in picked:
            groups[c.segments[0].episode].append(c)
        reviewed = []
        eps_sorted = sorted(groups)
        batches = [eps_sorted[i:i + max(1, batch)] for i in range(0, len(eps_sorted), max(1, batch))]
        log(f"[llm] reviewing {len(picked)} shortlisted candidates from {len(groups)} episodes in "
            f"{len(batches)} calls ({batch} episodes/call) with {llm.describe()}")
        for b in batches:
            cands = []
            for ep_num in b:
                top = sorted(groups[ep_num], key=lambda c: -c.score)[:6]
                if not getattr(llm, "batch_ok", True):
                    top = top[:4]
                cands += top
            kept, note = judge_episode(llm, title, b[0] if len(b) == 1 else f"{b[0]}-{b[-1]}", cands, idx, n, log=log)
            reviewed += kept
            judge_log.append(note)
        picked = nms(reviewed, thr=0.35)
        for c in picked:
            c.qa = check(idx, c)

    # trailers: one that stays early (no late spoilers, lands viewers on ep 1) and a full-arc one
    early = max(6, min(n, round(n * 0.25)))
    for m in (montage_windows(idx, max_ep=early, name="trailer_early"),
              montage_windows(idx, name="trailer_full_arc")):
        for c in m:
            _score(idx, c, len(premise), n)
            c.score += 0.3 * (len(c.segments) - 1)   # multi-cut is the point of a trailer
            c.qa = check(idx, c)
            picked.append(c)
    log(f"[select] {len(picked)} promos after de-duplication; "
        f"{sum(c.qa['pass'] for c in picked)} pass all automatic QA checks")

    ranked = rank(picked, n)
    for r, c in enumerate(ranked, 1):
        c.rank = r
        c.pid = f"P{r:03d}"
        c.desc = describe(idx, c)
        llm_out = getattr(c, "llm", None)
        if llm_out:
            for k in ("hook", "cliffhanger", "why"):
                if llm_out.get(k):
                    c.desc[f"{k}_rules"] = c.desc[k]
                    c.desc[k] = llm_out[k]
        c.post_reason = post_reason(c, n)
    if use_llm:
        from .judge import rank_show
        ranked, _ = rank_show(llm, title, ranked, log=log)
        for r, c in enumerate(ranked, 1):
            c.rank = r
    log(f"[done] {'rules + LLM' if use_llm else 'rules-only'} pass in {time.time()-t0:.1f}s")
    return {"title": title, "episodes": n, "premise_terms": sorted(premise), "promos": ranked,
            "index": idx, "judge_log": judge_log, "candidates_considered": len(all_c)}


def default_title(path):
    """'.../I Married a Broken Soldier. He was a Duke-script.zip' -> 'I Married a Broken Soldier. He was a Duke'."""
    base = os.path.basename(os.path.normpath(path))
    if base.lower().endswith(".zip"):
        base = base[:-4]
    elif os.path.isdir(path):
        subs = [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d)) and not d.startswith((".", "__"))]
        txts = [f for f in os.listdir(path) if f.lower().endswith(".txt")]
        if len(subs) == 1 and not txts:
            base = subs[0]
    return re.sub(r"[-_ ]?(script|scripts|screenplay)s?$", "", base, flags=re.I).strip() or "Untitled show"


def _proper_names(eps):
    """Character/place names: tokens that appear in speaker labels or are always capitalised."""
    names, cap, low = set(), Counter(), Counter()
    for e in eps:
        for b in e.beats:
            if b.kind == "dialogue":
                names |= {w for w in re.findall(r"[A-Za-z]+", b.speaker) if len(w) > 3}
            for w in re.findall(r"\b[A-Za-z][a-z']+\b", b.text + " " + e.logline):
                (cap if w[0].isupper() else low)[w.lower()] += 1
    names |= {w for w, k in cap.items() if k >= 3 and low[w] == 0}
    generic = {"duke", "duchess", "lady", "lord", "mother", "father", "soldier", "wedding", "married"}
    return {x.lower() for x in names} - generic


def rank(cands, n_eps):
    """Posting order = promo quality, nudged toward early episodes and away from ad-policy risk."""
    if not cands:
        return []
    scores = [c.score for c in cands]
    lo, hi = min(scores), max(scores)
    for c in cands:
        q = (c.score - lo) / (hi - lo + 1e-9)               # 0..1 promo quality (rules)
        llm = getattr(c, "llm", None)
        if llm and isinstance(llm.get("overall"), (int, float)):
            q = 0.4 * q + 0.6 * max(0.0, min(1.0, llm["overall"] / 10))
        funnel = c.parts.get("funnel", 0)
        risk = 0.10 * len(c.parts.get("safety", []))       # flag for review, don't bury
        qa_pen = 0.0 if getattr(c, "qa", {}).get("pass", True) else 0.15
        c.post_score = round(0.75 * q + 0.15 * funnel + 0.10 * min(c.parts.get("premise", 0) / 3, 1)
                             - risk - qa_pen, 4)
    ordered = sorted(cands, key=lambda c: -c.post_score)
    # keep the posting plan varied: in the top 15 no episode's footage is used twice (trailers exempt) -
    # two promos of the same scene in one week is wasted reach
    head, tail, starts, used = [], [], set(), Counter()
    for c in ordered:
        if len(head) >= 15:
            tail.append(c)
            continue
        if c.kind == "montage":
            head.append(c)
            continue
        start = c.segments[0].episode
        if start in starts or any(used[e] >= 1 for e in c.episodes):
            tail.append(c)
            continue
        head.append(c)
        starts.add(start)
        used.update(c.episodes)
    return head + sorted(tail, key=lambda c: -c.post_score)
