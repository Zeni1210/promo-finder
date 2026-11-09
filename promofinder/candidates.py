"""Turn scored beats into promo candidates.

Three candidate shapes, all built from the same scored beats:
  single   - one continuous stretch, 20-60 s, hook in first 3 s, ends on a cliff
  stitched - 2-4 segments from one story moment that is too long/slow to post raw:
             keep the hook, the cliff, and the hottest lines in between
  cross    - hook late in episode N, cliff in episode N+1 (only when the next
             episode's continuity notes say it picks up directly)

Timestamps are always taken from the screenplay, never generated: start/end
points are snapped to dialogue and camera-shot boundaries so an editor can cut
on a clean shot without clipping a line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .parse import Episode, fmt
from . import signals as S

MIN_LEN, MAX_LEN = 20.0, 60.0
SWEET_LO, SWEET_HI = 28.0, 45.0
HOOK_WINDOW = 3.0
PREROLL = 0.25
HOLD = 0.6          # let the cliff land before the cut


@dataclass
class Unit:
    """A speakable/seeable moment: a dialogue line, action, or sound beat (camera beats are cut points)."""
    ep: int
    start: float
    end: float
    kind: str
    speaker: str
    text: str
    tone: str
    sig: dict
    hook: float
    cliff: float
    inten: float
    beat_idx: int


@dataclass
class Segment:
    episode: int
    start: float
    end: float

    @property
    def dur(self):
        return round(self.end - self.start, 2)

    def to_dict(self):
        return {"episode": self.episode, "start": fmt(self.start), "end": fmt(self.end),
                "start_sec": round(self.start, 2), "end_sec": round(self.end, 2), "duration": self.dur}


@dataclass
class Candidate:
    kind: str
    segments: list
    units: list                     # units inside the promo, in play order
    hook_unit: Unit
    cliff_unit: Unit
    parts: dict = field(default_factory=dict)
    score: float = 0.0
    flags: list = field(default_factory=list)

    @property
    def duration(self):
        return round(sum(s.dur for s in self.segments), 2)

    @property
    def episodes(self):
        return sorted({s.episode for s in self.segments})


class EpisodeIndex:
    """Per-episode lookup: scored units + legal cut points."""

    def __init__(self, ep: Episode, premise: set):
        self.ep = ep
        self.units: list[Unit] = []
        self.dialogue = []
        cut_pts = {0.0, ep.duration}
        for b in ep.beats:
            if b.kind == "camera":
                cut_pts.add(round(b.start, 2))
                cut_pts.add(round(b.end, 2))
                continue
            sig = S.beat_signals(b, premise)
            u = Unit(ep.number, b.start, b.end, b.kind, b.speaker, b.text, b.tone, sig,
                     S.hook_score(sig), S.cliff_score(sig), S.intensity(sig), b.idx)
            self.units.append(u)
            if b.kind == "dialogue":
                self.dialogue.append(u)
        # camera beats also carry visual hooks ("slap", "explodes") at reduced weight
        self.cam_hook = []
        for b in ep.beats:
            if b.kind == "camera":
                sig = S.beat_signals(b, premise)
                self.cam_hook.append((b.start, 0.5 * S.hook_score(sig)))
        self.cuts = sorted(cut_pts)
        self.units.sort(key=lambda u: (u.start, u.beat_idx))
        self._contextualise()

    def _contextualise(self):
        us = self.units
        for k, u in enumerate(us):
            # a question whose answer lands within 3.5 s hooks as a pair ("Why are you holding her hand?"
            # / "Because he is marrying me.")
            if u.kind == "dialogue" and u.sig["question"]:
                reply = next((v for v in us[k + 1:k + 4] if v.kind == "dialogue" and v.speaker != u.speaker
                              and v.start - u.end <= 2.5), None)
                if reply is not None:
                    u.hook += 0.8 * max(reply.hook, 0)
            # a question only hangs if something is at stake around it
            prev = [v for v in us if u.start - 10 <= v.start < u.start]
            stakes = sum(v.inten for v in prev) / 10.0
            if u.sig["question"]:
                n = len(u.text.split())
                trivial = (u.text.strip().endswith("?") and n <= 3
                           and not (u.sig["reveal"] or u.sig["insult"] or u.sig["threat"]))
                u.cliff += min(1.5, stakes) - (1.0 if trivial else 0.0)
            else:
                u.cliff += 0.5 * min(1.5, stakes)
        # the last spoken line / beat of an episode is the writer's cliffhanger
        tail = [u for u in us if u.start >= self.ep.duration - 12]
        if tail:
            best = max(tail, key=lambda u: u.cliff)
            if best.sig["resolve"] <= 0.5:
                best.cliff += 1.5
                best.sig["episode_cliff"] = True
        self.recap = set()

    # -- boundary helpers ---------------------------------------------------
    def line_at(self, t: float):
        for d in self.dialogue:
            if d.start < t < d.end - 0.05:
                return d
        return None

    def safe_start(self, hook_start: float):
        t = max(0.0, hook_start - PREROLL)
        for _ in range(4):                      # back up until no line is clipped
            d = self.line_at(t)
            if d is None:
                break
            t = d.start
        # snap back to a shot boundary if one is very close (clean cut, no dead air)
        prev = [c for c in self.cuts if t - 0.6 <= c <= t]
        if prev:
            cand = max(prev)
            if self.line_at(cand) is None:
                t = cand
        return round(t, 2)

    def safe_end(self, cliff_end: float, nxt_start: float | None):
        t = min(self.ep.duration, cliff_end + HOLD)
        if nxt_start is not None:
            t = min(t, max(cliff_end + 0.1, nxt_start - 0.05))
        for _ in range(4):
            d = self.line_at(t)
            if d is None:
                break
            if d.start > cliff_end - 0.01:
                # stop before the next line (exactly at its first frame if it starts back-to-back)
                t = d.start if d.start - cliff_end < 0.1 else max(cliff_end, d.start - 0.05)
            else:
                t = d.end + 0.05                               # overlapping line: let it finish
        nxt = [c for c in self.cuts if t <= c <= t + 1.0 and (nxt_start is None or c <= nxt_start)]
        if nxt and self.line_at(min(nxt)) is None:
            t = min(nxt)
        return round(min(t, self.ep.duration), 2)

    def anchor(self, h: "Unit") -> "Unit":
        """Where a promo built on hook h should open. A short echo-question ("Poison?") only lands
        if the line it echoes ("Someone is poisoning her.") is in the clip, so open on that line."""
        if h.kind != "dialogue" or not h.text.strip().endswith("?") or len(h.text.split()) > 3:
            return h
        k = self.units.index(h)
        prev = next((v for v in reversed(self.units[max(0, k - 4):k]) if v.kind == "dialogue"), None)
        if prev is not None and prev.speaker != h.speaker and h.start - prev.end <= 3.0 \
                and h.start - prev.start <= 6.0:
            return prev
        return h

    def next_dialogue_start(self, after: float):
        for d in self.dialogue:
            if d.start > after + 0.01:
                return d.start
        return None

    def units_between(self, a: float, b: float):
        return [u for u in self.units if u.start >= a - 0.01 and u.start < b]

    def hook_in(self, a: float):
        """Best hook among units/camera beats starting within the first 3 s."""
        best, bu = -9, None
        for u in self.units:
            if a - 0.01 <= u.start <= a + HOOK_WINDOW:
                if u.hook > best:
                    best, bu = u.hook, u
        cam = max([h for (t, h) in self.cam_hook if a - 0.01 <= t <= a + HOOK_WINDOW], default=-9)
        return max(best, cam), bu


# ---------------------------------------------------------------------------
def _score(idx_map, cand: Candidate, premise_n: int, ep_count: int):
    units = cand.units
    if not units:
        return -99
    dur = cand.duration
    first_seg = cand.segments[0]
    ix0 = idx_map[first_seg.episode]
    hook, hu = ix0.hook_in(first_seg.start)
    if hu is not None:
        cand.hook_unit = hu
    cl = cand.cliff_unit
    last_seg = cand.segments[-1]
    ixl = idx_map[last_seg.episode]
    tail = [u for u in units if u.start >= last_seg.end - 3.5]
    cliff = cl.cliff + 0.4 * max([u.cliff for u in tail], default=0)
    if last_seg.end >= ixl.ep.duration - 6.0:
        # writers put the episode's cliffhanger in the last seconds
        cliff += 0.8 if cl.sig["resolve"] <= 0.5 else 0.0
    density = sum(u.inten for u in units) / max(dur, 1.0)
    # lull: longest stretch with no line/action/sfx
    lull = 0.0
    for seg in cand.segments:
        ts = [seg.start] + sorted(u.start for u in units if seg.start <= u.start < seg.end) + [seg.end]
        lull = max(lull, max(b - a for a, b in zip(ts, ts[1:])))
    lull_pen = max(0.0, lull - 4.0) * 0.35
    first_line = next((u for u in units if u.kind == "dialogue"), None)
    standalone = 0.0
    if first_line is not None and S.starts_mid_thought(first_line.text):
        standalone -= 1.2
    if first_line is not None and first_line.start - first_seg.start > 6:
        standalone -= 0.4
    names = set()
    for u in units:
        if u.kind == "dialogue":
            names |= set(re.findall(r"\b([A-Z][a-z]{3,})\b", u.text)) - {"Lady", "Lord", "Your", "Grace",
                                                                         "Father", "Mother", "Please", "Then",
                                                                         "What", "Look", "Enough", "There"}
    if len(names) > 3:
        standalone -= 0.3 * (len(names) - 3)
    if dur < SWEET_LO:
        dur_bonus = -0.08 * (SWEET_LO - dur)
    elif dur > SWEET_HI:
        dur_bonus = -0.05 * (dur - SWEET_HI)
    else:
        dur_bonus = 0.5
    seg_pen = 0.3 * (len(cand.segments) - 1)
    premise = min(3.0, sum(u.sig["premise"] for u in units) / 3.0)
    # early footage converts to "watch ep 1" better and spoils less; average over every cut so a
    # trailer that reaches episode 45 is scored as reaching episode 45
    funnel = sum(1.0 - (s.episode - 1) / max(ep_count - 1, 1) for s in cand.segments) / len(cand.segments)
    safety = S.safety_flags([u.text for u in units])
    cand.flags = sorted(set(cand.flags) | {f"ad_safety:{f}" for f in safety})
    total = (1.0 * min(hook, 8) + 1.0 * min(cliff, 8) + 5.0 * density + standalone
             + dur_bonus - lull_pen - seg_pen + 0.35 * premise)
    cand.parts = {"hook": round(hook, 2), "cliff": round(cliff, 2), "density": round(density, 3),
                  "standalone": round(standalone, 2), "duration_fit": round(dur_bonus, 2),
                  "lull_sec": round(lull, 1), "segments_penalty": seg_pen, "premise": round(premise, 2),
                  "funnel": round(funnel, 2), "safety": safety}
    cand.score = round(total, 3)
    return total


def single_windows(ix: EpisodeIndex, hook_min: float = 2.0):
    out = []
    us = ix.units
    for i, h in enumerate(us):
        if h.hook < hook_min:
            continue
        a = ix.safe_start(ix.anchor(h).start)
        if h.start - a > HOOK_WINDOW + 3.0 or ix.anchor(h).hook + 0.6 * h.hook < hook_min:
            continue
        for j in range(i + 1, len(us)):
            c = us[j]
            if c.end - a > MAX_LEN - HOLD:
                break
            if c.end - a < MIN_LEN - HOLD:
                continue
            if c.cliff < 0.8:
                continue
            nxt = ix.next_dialogue_start(c.end - 0.01)
            b = ix.safe_end(c.end, nxt)
            if not (MIN_LEN <= b - a <= MAX_LEN):
                continue
            seg = Segment(ix.ep.number, a, b)
            out.append(Candidate("single", [seg], ix.units_between(a, b), h, c))
    return out


def _exchanges(units):
    """Group a question with its immediate answer so compression never keeps one without the other."""
    groups, cur = [], []
    for u in units:
        if cur and (u.start - cur[-1].end > 1.2 or not (cur[-1].sig["question"] or cur[-1].kind != "dialogue")):
            groups.append(cur)
            cur = []
        cur.append(u)
    if cur:
        groups.append(cur)
    return groups


def compress(ixs: list, h: Unit, c: Unit, kind: str, target=(32.0, 55.0), max_segs=4, exclude=frozenset()):
    """Keep hook + cliff + highest-intensity exchanges between them, in order, within 60 s."""
    # timeline of (ix, unit)
    seq = []
    for ix in ixs:
        for u in ix.units:
            if id(u) not in exclude:
                seq.append((ix, u))
    pos = {id(u): k for k, (_, u) in enumerate(seq)}
    if id(h) not in pos or id(c) not in pos:
        return None
    h_ix = next(ix for ix in ixs if ix.ep.number == h.ep)
    h0 = h_ix.anchor(h)
    hi, ci = pos.get(id(h0), pos[id(h)]), pos[id(c)]
    if ci <= hi:
        return None
    middle = [u for _, u in seq[hi + 1:ci]]
    groups = _exchanges(middle)
    # rank groups by heat per second
    ranked = sorted(groups, key=lambda g: -(sum(x.inten + 0.6 * x.hook for x in g) /
                                             max(g[-1].end - g[0].start, 1.5)))
    keep = {id(h), id(c), id(h0)}

    def est_len(keep_ids):
        segs = _segments_from(seq, keep_ids)
        return sum(s.end - s.start for s in segs), segs

    total, segs = est_len(keep)
    for g in ranked:
        trial = keep | {id(x) for x in g}
        t2, s2 = est_len(trial)
        if t2 > target[1] or len(s2) > max_segs:
            continue
        keep, total, segs = trial, t2, s2
        if total >= target[0]:
            break
    if not (MIN_LEN <= total <= MAX_LEN) or len(segs) < 2:
        return None
    # what the viewer actually sees = every beat inside the cuts, not just the ones we chose
    by_ep = {ix.ep.number: ix for ix in ixs}
    units = []
    for sg in segs:
        units += [u for u in by_ep[sg.episode].units_between(sg.start, sg.end) if id(u) not in exclude]
    return Candidate(kind, segs, units, h, c)


def _segments_from(seq, keep_ids):
    """Merge kept units into cut-safe segments (gap <= 1.5 s within an episode merges)."""
    kept = [(ix, u) for ix, u in seq if id(u) in keep_ids]
    segs = []
    for ix, u in kept:
        a = ix.safe_start(u.start) if not segs or segs[-1][0] is not ix or u.start - segs[-1][2] > 1.5 else None
        if a is None:
            segs[-1][2] = max(segs[-1][2], u.end)
            segs[-1][3] = u
        else:
            segs.append([ix, a, u.end, u])
    out = []
    for ix, a, e, last in segs:
        nxt = ix.next_dialogue_start(e - 0.01)
        b = ix.safe_end(e, nxt)
        out.append(Segment(ix.ep.number, a, b))
    # drop overlaps produced by snapping
    clean = []
    for s in out:
        if clean and clean[-1].episode == s.episode and s.start <= clean[-1].end + 0.3:
            clean[-1] = Segment(s.episode, clean[-1].start, max(clean[-1].end, s.end))
        else:
            clean.append(s)
    return clean


def stitched_windows(ix: EpisodeIndex, hook_min=3.0, cliff_min=2.0):
    out = []
    hooks = [u for u in ix.units if u.hook >= hook_min]
    cliffs = [u for u in ix.units if u.cliff >= cliff_min]
    for h in hooks:
        for c in cliffs:
            span = c.end - h.start
            if 55 <= span <= 160:
                cand = compress([ix], h, c, "stitched")
                if cand:
                    out.append(cand)
    return out


DIRECT = re.compile(r"(directly|immediately|continuous|picks up (right|directly|immediately)|"
                    r"follows directly|picking up immediately)", re.I)


def cross_windows(ix_a: EpisodeIndex, ix_b: EpisodeIndex, hook_min=3.0, cliff_min=2.0):
    """Hook in the last ~75 s of episode A, cliff in the first ~90 s of episode B."""
    if not DIRECT.search(ix_b.ep.continuity or ""):
        return []
    out = []
    recap = recap_units(ix_a, ix_b)
    hooks = [u for u in ix_a.units if u.hook >= hook_min and u.start >= ix_a.ep.duration - 75]
    cliffs = [u for u in ix_b.units if u.cliff >= cliff_min and u.end <= 95 and id(u) not in recap]
    for h in hooks:
        for c in cliffs:
            cand = compress([ix_a, ix_b], h, c, "cross_episode", max_segs=3, exclude=recap)
            if cand and len(cand.episodes) == 2:
                out.append(cand)
    return out


def _toks(t):
    return set(re.findall(r"[a-z']+", t.lower())) - {"the", "a", "and", "to", "of", "you", "i", "is", "her", "his"}


def recap_units(ix_a: EpisodeIndex, ix_b: EpisodeIndex, head=40.0, tail=60.0, thr=0.5):
    """Lines at the top of episode B that re-play the end of episode A (micro-drama recaps)."""
    end_a = [_toks(u.text) for u in ix_a.units if u.start >= ix_a.ep.duration - tail and u.kind == "dialogue"]
    out = set()
    for u in ix_b.units:
        if u.start > head:
            break
        if u.kind != "dialogue":
            continue
        tb = _toks(u.text)
        if tb and any(len(tb & ta) / len(tb | ta) >= thr for ta in end_a if ta):
            out.add(id(u))
    return frozenset(out)


def iou(a: Candidate, b: Candidate) -> float:
    inter = 0.0
    for s in a.segments:
        for t in b.segments:
            if s.episode == t.episode:
                inter += max(0.0, min(s.end, t.end) - max(s.start, t.start))
    union = a.duration + b.duration - inter
    return inter / union if union > 0 else 0.0


def same_open(a: Candidate, b: Candidate) -> bool:
    """Two promos that open on the same shot are the same promo to a viewer."""
    sa, sb = a.segments[0], b.segments[0]
    return sa.episode == sb.episode and abs(sa.start - sb.start) < 1.0


def nms(cands, thr=0.35, per_key_limit=None, key=lambda c: tuple(c.episodes)):
    kept, counts = [], {}
    for c in sorted(cands, key=lambda c: -c.score):
        if any(iou(c, k) > thr or same_open(c, k) for k in kept):
            continue
        k = key(c)
        if per_key_limit and counts.get(k, 0) >= per_key_limit:
            continue
        counts[k] = counts.get(k, 0) + 1
        kept.append(c)
    return kept


# ---------------------------------------------------------------------------
# Montage trailers: the ReelShort/DramaBox "story so far" cut - set-up, escalation,
# and a hanging question, pulled from different episodes.
def _moment(ix: EpisodeIndex, u: Unit, max_len=12.0):
    """A short, self-contained beat around unit u: the line + its reply, cut-safe."""
    a = ix.safe_start(u.start)
    k = ix.units.index(u)
    end = u.end
    for v in ix.units[k + 1:k + 4]:
        if v.start - end > 1.5 or v.end - a > max_len:
            break
        end = v.end
        if v.kind == "dialogue" and v.speaker != u.speaker:
            break                       # stop after the first reply
    nxt = ix.next_dialogue_start(end - 0.01)
    b = ix.safe_end(end, nxt)
    if b - a < 3.0:
        return None
    return Segment(ix.ep.number, a, b)


def montage_windows(idx: dict, max_ep: int | None = None, max_total=55.0, name="montage"):
    nums = sorted(idx)
    if max_ep:
        nums = [n for n in nums if n <= max_ep]
    if len(nums) < 4:
        return []
    pool = []
    for n in nums:
        ix = idx[n]
        for u in ix.units:
            if u.hook >= 2.5 or u.cliff >= 3.0:
                seg = _moment(ix, u)
                if seg:
                    prem = sum(v.sig["premise"] for v in ix.units_between(seg.start, seg.end))
                    pool.append((n, u, seg, prem))
    if not pool:
        return []
    third = nums[max(0, len(nums) // 8)]
    openers = [p for p in pool if p[0] <= max(third, nums[0] + 2)]
    opener = max(openers, key=lambda p: p[1].hook + 0.5 * p[3])
    closers = [p for p in pool if p[0] > opener[0] + 2 and (p[1].sig["question"] or p[1].sig["threat"]
                                                             or p[1].sig.get("denial"))]
    if not closers:
        return []
    closer = max(closers, key=lambda p: p[1].cliff + 0.8 * p[3])
    middle = [p for p in pool if opener[0] < p[0] < closer[0]]
    middle.sort(key=lambda p: -(p[1].hook + 0.6 * p[3]))
    chosen, used_eps = [opener, closer], {opener[0], closer[0]}
    total = opener[2].dur + closer[2].dur
    for p in middle:
        if p[0] in used_eps or total + p[2].dur > max_total or len(chosen) >= 5:
            continue
        chosen.append(p)
        used_eps.add(p[0])
        total += p[2].dur
    chosen.sort(key=lambda p: (p[0], p[2].start))
    if total < MIN_LEN:
        return []
    segs = [p[2] for p in chosen]
    units = []
    for p in chosen:
        units += idx[p[0]].units_between(p[2].start, p[2].end)
    c = Candidate("montage", segs, units, opener[1], closer[1])
    c.flags.append(name)
    return [c]
