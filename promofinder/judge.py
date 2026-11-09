"""Optional LLM pass: an 'editor' model reviews the rules' shortlist for each episode.

The model never touches timestamps. It sees numbered beats and returns beat ids;
code converts ids to cut-safe timestamps, re-runs every QA check, and falls back
to the rules version if the model's edit breaks a constraint. That is the main
defence against hallucinated timecodes and invented dialogue.
"""
from __future__ import annotations

import json
import os

from .parse import fmt
from .candidates import Candidate, Segment, _score, MIN_LEN, MAX_LEN
from .qa import check

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _system():
    with open(os.path.join(HERE, "prompts", "judge_system.md"), encoding="utf-8") as f:
        return f.read()


def beat_id(u, ix):
    return f"E{ix.ep.number}.{ix.units.index(u)}"


def _beats_block(ix, lo, hi):
    rows = []
    for k, u in enumerate(ix.units):
        if lo <= u.start <= hi:
            who = u.speaker.title() + (f" ({u.tone})" if u.tone else "") if u.kind == "dialogue" else u.kind.upper()
            rows.append(f"E{ix.ep.number}.{k} [{fmt(u.start)}-{fmt(u.end)}] {who}: {u.text}")
    return "\n".join(rows)


def _cand_block(c, idx, cid):
    segs = []
    for s in c.segments:
        ix = idx[s.episode]
        inside = ix.units_between(s.start, s.end)
        if inside:
            segs.append(f'{{"from": "{beat_id(inside[0], ix)}", "to": "{beat_id(inside[-1], ix)}"}}')
    return f"{cid} ({c.kind}, {c.duration:.0f}s): segments [{', '.join(segs)}]"


def build_prompt(title, ep_num, cands, idx):
    anchors = sorted({c.segments[0].episode for c in cands})
    scope = (f"episode {anchors[0]}" if len(anchors) == 1 else
             f"episodes {', '.join(map(str, anchors))} (judge each episode's candidates separately)")
    eps = sorted({e for c in cands for e in c.episodes})
    blocks = []
    for e in eps:
        ix = idx[e]
        spans = [s for c in cands for s in c.segments if s.episode == e]
        lo = max(0.0, min(s.start for s in spans) - 12)
        hi = max(s.end for s in spans) + 8
        blocks.append(f"### Episode {e} (logline: {ix.ep.logline})\n{_beats_block(ix, lo, hi)}")
    cand_lines = "\n".join(_cand_block(c, idx, f"C{i+1}") for i, c in enumerate(cands))
    schema = {"promos": [{"candidate": "C1 or NEW", "keep": True, "segments": [{"from": "E1.10", "to": "E1.25"}],
                          "hook": "the opening beat, quoted or described", "cliffhanger": "the final beat",
                          "why": "one sentence", "scores": {"hook": 4, "cliffhanger": 5, "standalone": 3, "premise": 4},
                          "overall": 8, "risks": []}]}
    return (f"Show: {title}\nWe are choosing promos anchored in {scope}.\n\n"
            + "\n\n".join(blocks)
            + f"\n\nCandidate promos proposed by a rules engine:\n{cand_lines}\n\n"
            "Review every candidate. You may tighten or move its boundaries (using beat ids only), "
            "reject it, or propose at most one NEW promo per episode from the beats above if the rules missed a "
            "better one. Keep at most 3 per episode. Scores are 1-5; overall is 1-10 (10 = post this today).\n"
            f"Return JSON exactly like: {json.dumps(schema)}")


def _resolve(seg_spec, idx):
    """Beat ids -> Segment, or None if the ids are invalid."""
    try:
        a_ep, a_k = seg_spec["from"].lstrip("E").split(".")
        b_ep, b_k = seg_spec["to"].lstrip("E").split(".")
        a_ep, a_k, b_ep, b_k = int(a_ep), int(a_k), int(b_ep), int(b_k)
    except (KeyError, ValueError, AttributeError):
        return None
    if a_ep != b_ep or a_ep not in idx:
        return None
    ix = idx[a_ep]
    if not (0 <= a_k <= b_k < len(ix.units)):
        return None
    ua, ub = ix.units[a_k], ix.units[b_k]
    start = ix.safe_start(ua.start)
    end = ix.safe_end(ub.end, ix.next_dialogue_start(ub.end - 0.01))
    return Segment(a_ep, start, end), ua, ub, ix


def judge_episode(llm, title, ep_num, cands, idx, n_eps, log=print):
    if not cands:
        return [], {"episode": ep_num, "status": "no candidates"}
    prompt = build_prompt(title, ep_num, cands, idx)
    try:
        data = llm.json(_system(), prompt)
    except Exception as e:                                  # degrade, never crash the run
        log(f"[llm] episode {ep_num}: {e} -> keeping rules picks")
        for c in cands:
            c.flags.append("llm_failed")
        return cands, {"episode": ep_num, "status": f"error: {e}"}
    out, notes = [], []
    n_anchor = len({c.segments[0].episode for c in cands})
    mentioned = set()
    for item in data.get("promos", [])[:4 * n_anchor]:
        mentioned.add(str(item.get("candidate", "")))
        if not item.get("keep", True):
            notes.append(f"rejected {item.get('candidate')}: {item.get('why', '')}")
            continue
        base = None
        cid = str(item.get("candidate", ""))
        if cid.startswith("C") and cid[1:].isdigit() and int(cid[1:]) - 1 < len(cands):
            base = cands[int(cid[1:]) - 1]
        resolved = [_resolve(s, idx) for s in item.get("segments", [])]
        new = None
        edit_rejected = False
        if resolved and all(resolved):
            segs = [r[0] for r in resolved]
            units = []
            for r in resolved:
                units += r[3].units_between(r[0].start, r[0].end)
            ordered = all((a.episode, a.end) <= (b.episode, b.start + 0.01) for a, b in zip(segs, segs[1:]))
            if units and ordered:
                kind = ("single" if len(segs) == 1 else
                        "cross_episode" if len({s.episode for s in segs}) > 1 else "stitched")
                new = Candidate(kind, segs, units, resolved[0][1], resolved[-1][2])
                _score(idx, new, 0, n_eps)
                new.qa = check(idx, new)
                if not (MIN_LEN <= new.duration <= MAX_LEN) or not new.qa["pass"]:
                    notes.append(f"{cid}: model edit failed QA ({'; '.join(new.qa['issues'])[:120]}) -> using rules cut")
                    new, edit_rejected = None, True
        elif item.get("segments"):
            notes.append(f"{cid}: invalid beat ids from model -> using rules cut")
            edit_rejected = True
        c = new or base
        if c is None:
            continue
        if edit_rejected:
            # the model's text describes a cut we threw away: keep the rules cut *and* the rules text
            c.flags = sorted(set(c.flags) | {"llm_edit_rejected"})
            if not hasattr(c, "qa"):
                c.qa = check(idx, c)
            out.append(c)
            continue
        if new is not None and base is not None:
            new.flags = sorted(set(base.flags) | {"llm_edited"})
        c.llm = {"hook": item.get("hook"), "cliffhanger": item.get("cliffhanger"), "why": item.get("why"),
                 "scores": item.get("scores", {}), "overall": item.get("overall"), "risks": item.get("risks", []),
                 "source": cid}
        if not hasattr(c, "qa"):
            c.qa = check(idx, c)
        out.append(c)
    # a candidate the model never mentioned was not judged: keep the rules version rather than lose it
    silent = [c for i, c in enumerate(cands) if f"C{i+1}" not in mentioned]
    if silent:
        notes.append(f"model gave no verdict on {len(silent)} candidate(s) -> kept rules picks")
        for c in silent:
            c.flags = sorted(set(c.flags) | {"llm_no_verdict"})
        out += silent
    # at most 3 per anchor episode, in the model's order
    per, capped = {}, []
    for c in out:
        e = c.segments[0].episode
        if per.get(e, 0) < 3:
            capped.append(c)
            per[e] = per.get(e, 0) + 1
    return capped, {"episode": ep_num, "status": "ok", "notes": notes, "kept": len(capped)}


RANK_SYSTEM = ("You are planning which micro-drama promos to post first on Reels/TikTok/Shorts to acquire new viewers. "
               "Order matters: the first posts should sell the show's premise to a cold audience, lead viewers into "
               "episode 1 rather than spoil late twists, and be safe for ad platforms. Return JSON only.")


def rank_show(llm, title, promos, top=20, log=print):
    head = promos[:top]
    lines = []
    for c in head:
        d = c.desc
        lines.append(json.dumps({"id": c.pid, "type": c.kind, "cuts": d["cuts"], "duration": c.duration,
                                 "hook": d["hook"], "cliffhanger": d["cliffhanger"], "why": d["why"],
                                 "flags": c.flags + c.parts.get("safety", [])}, ensure_ascii=False))
    user = (f"Show: {title}\nCandidate promos (already quality-checked):\n" + "\n".join(lines) +
            '\n\nReturn {"order": [{"id": "P01", "reason": "one sentence on why it is posted at this position"}]} '
            "covering every id above exactly once.")
    try:
        data = llm.json(RANK_SYSTEM, user)
    except Exception as e:
        log(f"[llm] show ranking failed: {e} -> keeping rules order")
        return promos, False
    ids = {c.pid: c for c in head}
    seen, order = set(), []
    for row in data.get("order", []):
        pid = row.get("id")
        if pid in ids and pid not in seen:
            ids[pid].post_reason = row.get("reason") or ids[pid].post_reason
            order.append(ids[pid])
            seen.add(pid)
    order += [c for c in head if c.pid not in seen]       # anything the model dropped keeps its slot at the end
    return order + promos[top:], True
