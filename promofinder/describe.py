"""Human-readable fields for each promo: hook, cliffhanger, why-it-works, transcript.

In rules mode these are templated from the signals that fired, so every claim
points at something on the page. The LLM judge (if enabled) rewrites them.
"""
from __future__ import annotations

from .parse import fmt


def _speaker(u):
    if u.kind == "dialogue":
        return u.speaker.title()
    return {"action": "ACTION", "sfx": "SFX"}.get(u.kind, u.kind.upper())


def _line(u, n=110):
    t = u.text if len(u.text) <= n else u.text[:n - 1].rstrip() + "…"
    return f"{_speaker(u)}: {t}" if u.kind == "dialogue" else f"[{_speaker(u)}] {t}"


def hook_kind(u):
    s = u.sig
    opts = {"a physical attack": s["physical"] * (1.0 if u.kind != "dialogue" else 0.4),
            "a reveal": s["reveal"], "a public humiliation": s["insult"],
            "a shocked reaction": s["reaction"] * 0.8, "a sudden interruption": s["interrupt"] * 1.5,
            "a confrontational line": s["punch"] + s["heat"] * 0.4 + s["question"] * 0.5}
    return max(opts, key=opts.get)


def cliff_kind(u, at_ep_end: bool):
    s = u.sig
    opts = {"an unanswered question": s["question"] * 1.6, "an interruption": s["interrupt"] * 1.3,
            "a threat hanging in the air": s["threat"] * 1.0 + s["reveal"] * 0.3,
            "the episode's own cliffhanger beat": s["writer_cliff"] * 1.4 + (0.6 if at_ep_end else 0),
            "a moment of violence": s["physical"] * 0.6, "a shock reaction": s["reaction"] * 0.5}
    k = max(opts, key=opts.get)
    return k if opts[k] > 0 else "an unresolved beat"


def describe(idx, c) -> dict:
    h, cl = c.hook_unit, c.cliff_unit
    first = c.segments[0]
    last = c.segments[-1]
    at_end = last.end >= idx[last.episode].ep.duration - 6
    hook_at = max(0.0, h.start - first.start)
    names = []
    for s in c.segments:
        names.append(f"Ep{s.episode} {fmt(s.start)}-{fmt(s.end)}")
    shape = {"single": "one continuous cut", "stitched": f"{len(c.segments)} cuts from one scene",
             "cross_episode": f"{len(c.segments)} cuts across episodes {c.episodes[0]}-{c.episodes[-1]}",
             "montage": f"trailer of {len(c.segments)} moments from episodes "
                        f"{', '.join(str(e) for e in c.episodes)}"}[c.kind]
    why = (f"Opens on {hook_kind(h)} {hook_at:.1f}s in, then cuts on {cliff_kind(cl, at_end)}; "
           f"{shape}, {c.duration:.0f}s.")
    if c.parts.get("premise", 0) >= 1.5:
        why += " Sells the show's core premise."
    transcript = []
    seg_i = 0
    t_acc = 0.0
    for si, s in enumerate(c.segments):
        for u in c.units:
            if u.ep == s.episode and s.start - 0.01 <= u.start < s.end:
                transcript.append({"t": round(t_acc + (u.start - s.start), 1), "seg": si + 1,
                                   "line": _line(u)})
        t_acc += s.dur
    return {"hook": _line(h), "hook_at_sec": round(hook_at, 1), "cliffhanger": _line(cl),
            "why": why, "cuts": names, "transcript": transcript}


def post_reason(c, n_eps) -> str:
    """Why this promo sits at this position in the posting order (rules mode)."""
    p = c.parts
    first_ep = c.segments[0].episode
    bits = []
    if c.kind == "montage":
        bits.append("trailer cut: sets up the premise and escalates across episodes, so it works as the "
                    "'what is this show' post")
    if first_ep <= max(3, n_eps // 10):
        bits.append(f"from episode {first_ep}, so a new viewer lands at the start of the story")
    elif first_ep > n_eps * 0.7:
        bits.append(f"late-season (ep {first_ep}): strong, but spoils more and asks viewers to binge "
                    f"{first_ep - 1} episodes; post after the early hooks")
    if p.get("hook", 0) >= 5:
        bits.append("very strong first-3-second hook")
    if p.get("cliff", 0) >= 5:
        bits.append("hard cliffhanger ending")
    if p.get("premise", 0) >= 1.5:
        bits.append("sells the title premise (betrayed bride / secret Duke)")
    if p.get("standalone", 0) < 0:
        bits.append("needs some context, so weaker for cold audiences")
    if p.get("safety"):
        bits.append("needs ad-policy review: " + ", ".join(p["safety"]).replace("_", " "))
    if not getattr(c, "qa", {}).get("pass", True):
        bits.append("failed an automatic check; fix before posting")
    s = "; ".join(bits) if bits else "solid but unremarkable on every signal"
    return s[0].upper() + s[1:] + "."
