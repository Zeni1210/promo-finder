"""Automatic quality gates every promo must pass before an editor sees it.

These are the checks that are cheap to automate and expensive to get wrong
(a clipped line, a 63-second cut, a soft opening). Anything subjective - is it
*actually* gripping? - is left to the ranking, the LLM judge and the editor.
"""
from __future__ import annotations

from . import signals as S
from .candidates import MIN_LEN, MAX_LEN, HOOK_WINDOW


def check(idx, cand) -> dict:
    issues = []
    dur = cand.duration
    if not (MIN_LEN - 0.01 <= dur <= MAX_LEN + 0.01):
        issues.append(f"duration {dur:.1f}s outside 20-60s")
    prev = None
    for s in cand.segments:
        ix = idx[s.episode]
        if s.start < 0 or s.end > ix.ep.duration + 0.01 or s.end <= s.start:
            issues.append(f"segment out of bounds ep{s.episode} {s.start}-{s.end}")
        for t, where in ((s.start, "start"), (s.end, "end")):
            d = ix.line_at(t)
            if d is not None:
                issues.append(f"cuts a line at {where} of ep{s.episode} segment: \"{d.text[:40]}\"")
        if prev and prev.episode == s.episode and s.start < prev.end:
            issues.append("segments overlap / out of order")
        if prev and prev.episode > s.episode:
            issues.append("segments jump backwards across episodes")
        prev = s
    first = cand.segments[0]
    ix0 = idx[first.episode]
    hook, hu = ix0.hook_in(first.start)
    if hu is not None and ix0.anchor(hu) is not hu:
        hook = max(hook, ix0.anchor(hu).hook + 0.6 * hu.hook)
    if hook < 2.0:
        issues.append(f"weak opening: best beat in first {HOOK_WINDOW:.0f}s scores {hook:.1f} (<2.0)")
    first_any = min((u.start for u in cand.units), default=first.start)
    if first_any - first.start > 1.5:
        issues.append(f"dead air: nothing happens for {first_any-first.start:.1f}s at the open")
    cl = cand.cliff_unit
    if cl.sig["resolve"] > 0.6 and cl.sig["question"] == 0:
        issues.append(f"ending may resolve rather than hang: \"{cl.text[:50]}\"")
    fl = next((u for u in cand.units if u.kind == "dialogue"), None)
    if fl is not None and S.starts_mid_thought(fl.text):
        issues.append(f"opens mid-thought: \"{fl.text[:40]}\" (needs earlier context)")
    if len(cand.segments) > 1:
        tiny = [s for s in cand.segments if s.dur < 2.5]
        if tiny:
            issues.append(f"{len(tiny)} segment(s) shorter than 2.5s (jumpy)")
    warn = [f"ad-policy review: {f}" for f in cand.parts.get("safety", [])]
    return {"pass": not issues, "issues": issues, "warnings": warn}
