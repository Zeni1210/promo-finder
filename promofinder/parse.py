"""Parse Kuku-style timestamped screenplays into a flat list of timed beats.

Input format (one .txt per episode):
    EPISODE NUMBER: Episode 1
    EPISODE LOGLINE: ...
    CONTINUITY NOTES: ...
    --- SCRIPT START ---
    SCENE 01
    Scene heading: INT. CATHEDRAL - DAY
    Scene start: 00:00.00
    Scene end: 02:18.00
    **CAMERA:** / **ACTION:** / **SOUND FX:** / **SPEAKER (Tone):**
    Start: (00:00.00) | End: (00:04.80)
    <text>

The parser is deliberately forgiving: unknown block labels are treated as
dialogue speakers, missing scene headers fall back to a single scene, and
timestamps may be MM:SS.cc or HH:MM:SS.cc.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict

TS_RE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)")
BLOCK_RE = re.compile(r"^\*\*(.+?):\*\*\s*$")
TIME_RE = re.compile(r"Start:\s*\(([^)]+)\)\s*\|\s*End:\s*\(([^)]+)\)")
NON_DIALOGUE = {"CAMERA", "ACTION", "SOUND FX", "SFX", "MUSIC", "TEXT", "TITLE", "SUPER", "VO"}


def to_sec(ts: str) -> float:
    m = TS_RE.search(ts.strip())
    if not m:
        raise ValueError(f"bad timestamp: {ts!r}")
    h, mnt, s = m.groups()
    return (int(h) * 3600 if h else 0) + int(mnt) * 60 + float(s)


def fmt(sec: float) -> str:
    sec = max(0.0, sec)
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m:02d}:{s:05.2f}"


@dataclass
class Beat:
    idx: int
    kind: str          # camera | action | sfx | dialogue
    start: float
    end: float
    text: str
    speaker: str = ""  # normalised speaker name for dialogue
    tone: str = ""     # parenthetical, e.g. "Cold, Commanding"
    scene: int = 0
    offscreen: bool = False


@dataclass
class Scene:
    num: int
    heading: str
    start: float
    end: float


@dataclass
class Episode:
    number: int
    title: str
    logline: str
    continuity: str
    path: str
    scenes: list = field(default_factory=list)
    beats: list = field(default_factory=list)

    @property
    def duration(self) -> float:
        ends = [b.end for b in self.beats] + [s.end for s in self.scenes]
        return max(ends) if ends else 0.0

    def to_dict(self):
        d = asdict(self)
        d["duration"] = self.duration
        return d


def _split_label(label: str):
    """'LADY JULIANA (O.S.) (Cold, Commanding)' -> ('LADY JULIANA', 'Cold, Commanding', True)."""
    parens = re.findall(r"\(([^)]*)\)", label)
    name = re.sub(r"\([^)]*\)", "", label).strip()
    offscreen = False
    tones = []
    for p in parens:
        if re.fullmatch(r"\s*(O\.?S\.?|V\.?O\.?|off[- ]?screen|cont'?d)\s*", p, re.I):
            offscreen = True
        else:
            tones.append(p.strip())
    return name, ", ".join(tones), offscreen


def parse_episode(path: str) -> Episode:
    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    lines = raw.splitlines()

    def header(key):
        m = re.search(rf"^{key}:\s*(.*)$", raw, re.M)
        return m.group(1).strip() if m else ""

    title = header("EPISODE NUMBER") or os.path.basename(path)
    num_m = re.search(r"(\d+)", title) or re.search(r"episode-(\d+)", os.path.basename(path))
    number = int(num_m.group(1)) if num_m else 0
    ep = Episode(number=number, title=title, logline=header("EPISODE LOGLINE"),
                 continuity=header("CONTINUITY NOTES"), path=path)

    scene_num, heading, s_start = 0, "", None
    i = 0
    cur_scene = 0
    while i < len(lines):
        ln = lines[i].strip()
        m_scene = re.match(r"^SCENE\s+(\d+)", ln)
        if m_scene:
            scene_num = int(m_scene.group(1))
            heading, s_start, s_end = "", None, None
            j = i + 1
            while j < len(lines) and j < i + 6:
                l2 = lines[j].strip()
                if l2.lower().startswith("scene heading:"):
                    heading = l2.split(":", 1)[1].strip()
                elif l2.lower().startswith("scene start:"):
                    s_start = to_sec(l2.split(":", 1)[1])
                elif l2.lower().startswith("scene end:"):
                    s_end = to_sec(l2.split(":", 1)[1])
                j += 1
            ep.scenes.append(Scene(scene_num, heading, s_start or 0.0, s_end or 0.0))
            cur_scene = scene_num
            i += 1
            continue
        m_block = BLOCK_RE.match(ln)
        if m_block and i + 1 < len(lines):
            label = m_block.group(1).strip()
            m_time = TIME_RE.search(lines[i + 1])
            if not m_time:
                i += 1
                continue
            start, end = to_sec(m_time.group(1)), to_sec(m_time.group(2))
            # text = following non-empty lines until blank line or next block
            text_lines = []
            j = i + 2
            while j < len(lines) and lines[j].strip() and not BLOCK_RE.match(lines[j].strip()) \
                    and not lines[j].startswith("SCENE ") and not lines[j].startswith("---"):
                text_lines.append(lines[j].strip())
                j += 1
            text = " ".join(text_lines).strip()
            name, tone, off = _split_label(label)
            up = name.upper()
            if up in ("CAMERA",):
                kind = "camera"
            elif up in ("ACTION",):
                kind = "action"
            elif up in ("SOUND FX", "SFX", "MUSIC"):
                kind = "sfx"
            else:
                kind = "dialogue"
                if "OFF-SCREEN" in up or "OFFSCREEN" in up:
                    off = True
                text = text.strip().strip('"').strip("“”")
            ep.beats.append(Beat(idx=len(ep.beats), kind=kind, start=start, end=max(end, start),
                                 text=text, speaker=name if kind == "dialogue" else "",
                                 tone=tone, scene=cur_scene or 1, offscreen=off))
            i = j
            continue
        i += 1

    if not ep.scenes:
        ep.scenes.append(Scene(1, "", 0.0, ep.duration))
    # stable chronological order (screenplays interleave camera/action/dialogue)
    ep.beats.sort(key=lambda b: (b.start, b.idx))
    for k, b in enumerate(ep.beats):
        b.idx = k
    return ep


def load_show(path: str) -> list:
    """Accepts a folder of .txt episodes (searched recursively), a single .txt, or a .zip of them."""
    if path.lower().endswith(".zip"):
        import tempfile
        import zipfile
        tmp = tempfile.mkdtemp(prefix="promofinder_")
        with zipfile.ZipFile(path) as z:
            for m in z.infolist():
                name = os.path.normpath(m.filename)
                if name.lower().endswith(".txt") and not name.startswith(("..", "/")) and "__MACOSX" not in name:
                    z.extract(m, tmp)
        path = tmp
    if os.path.isfile(path):
        files = [path]
    else:
        files = [os.path.join(r, f) for r, _, fs in os.walk(path) for f in fs
                 if f.lower().endswith(".txt") and not f.startswith(".")]
    eps = [parse_episode(f) for f in sorted(files)]
    eps = [e for e in eps if e.beats]
    # episode numbers missing or duplicated -> fall back to file order
    nums = [e.number for e in eps]
    if len(set(nums)) != len(nums) or 0 in nums:
        for k, e in enumerate(sorted(eps, key=lambda e: e.path), 1):
            e.number = k
    eps.sort(key=lambda e: e.number)
    return eps
