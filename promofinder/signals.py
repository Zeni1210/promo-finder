"""Cheap, transparent signals computed per beat.

Everything here is rules: no model calls. The goal is not to be clever but to
be *explainable* - every score can be traced back to the words that fired it,
which is what lets an editor trust (or overrule) a pick in seconds.

Lexicons are deliberately genre-generic for vertical microdrama (revenge,
romance, betrayal, hidden identity). Show-specific words come in at run time
from the show title + loglines (see `premise_terms`).
"""
from __future__ import annotations

import re
from collections import Counter

# --- stop-scroll moments: physical, visible, loud -------------------------
PHYSICAL = {
    r"\bslap": 3, r"\bstrik(e|es|ing)\b|\bstruck\b": 3, r"\bpunch": 3, r"\bshove": 2.5,
    r"\bdrag(s|ged|ging)?\b": 2, r"\bseiz": 2, r"\bgrab": 1.5, r"\byank": 2.5, r"\bwhip": 3,
    r"riding crop": 3, r"\bclub\b": 1.5, r"branding|red-hot|searing|glowing tip": 3,
    r"\bchain(s|ed)?\b|shackle|manacle": 1.5, r"\bcollaps": 2, r"\bcrash": 2,
    r"explo(de|des|sion)|detonat|fireball": 4, r"\bfire\b|blaze|inferno|flames": 2,
    r"\bblood|bleed|lacerat|gash": 2, r"tackl": 2, r"throat|choke|sleeper hold": 2.5,
    r"kicked open|burst(s)? open|fly open|slam": 2, r"\bknife|blade|sword|halberd|rifle|gun\b": 1.5,
    r"drops? (on)?to (one|her|his) knees?|sinks to one knee|drops to one knee": 2,
    r"\bthrows?\b|flings?|hurls?": 2, r"\bkiss": 1.5, r"\bshatter": 2, r"\bsnatch": 1.5,
}
# --- reveals / stakes: things a stranger instantly understands -------------
REVEAL = {
    r"\bpoison": 3, r"\barsenic": 3, r"\balive\b": 2, r"\bdead\b|\bdied\b|\bdie\b": 1.5,
    r"\bduke\b|\bduchess\b": 2, r"\bmarry(ing)?\b|\bwedding\b|\bwife\b|\bhusband\b|\bbride\b": 1,
    r"\bwho are you\b": 2.5, r"\bit'?s you\b|it was always me": 3, r"\bring\b|\bcrest\b": 1,
    r"\bdisowned\b|dead to the": 2.5, r"\bexecut|gallows|\bhang(s|ed)?\b|noose": 2.5,
    r"\bwitch": 2, r"\barrest": 2, r"\bimpost(e|o)r\b": 2, r"\btraitor|treason|betray": 2,
    r"\bstole|\bsteal|\bthief|\btheft": 1.5, r"\bson\b": 1, r"\bburied\b": 2,
    r"not (his|your) lawful wife|never my duchess": 3, r"\bpaid us\b|who sent you": 2.5,
    r"\bcondemn": 2.5, r"\bsecret": 1, r"\bguards?!": 1.5, r"\bkill": 2,
}
# genre staples: betrayal, vows, hidden identity (shared by most revenge/romance microdramas)
REVEAL.update({
    r"\byou knew\b": 2, r"\bswore\b|\byou promised\b|gave me your word": 2, r"changed my mind": 2,
    r"\byou let me\b": 1.5, r"marrying me|marry (me|her)\b|never going to marry": 2, r"holding her hand": 2,
    r"\bturn around\b": 1, r"abandon|cast (me|you|her) out|\bleft me\b": 1.5, r"better choice|\bchose\b": 1,
    r"\bliar\b|\blying\b|\bthat is a lie\b": 1.5, r"\bframe|\bplanted\b": 2,
    r"accept my hand|be my wife|offering it back|you have my (protection|word)|pronounce you": 2,
    r"\bi love\b|loved you": 1.5,
    r"resembl": 2.5, r"\bthe late (duke|duchess)\b": 2.5, r"\bghosts?\b": 1, r"lost son|my son\b": 2,
    r"same (kind of )?scar": 2, r"who (he|she|you) really|true (name|identity)|\brightful\b": 2,
    r"you already know": 2.5,
    r"save yourself|\brenounce|\bconfess|\bguilty\b|\bsentenc|\bverdict\b|\baccused\b|\bon trial\b": 1.5,
})
PHYSICAL.update({r"\bsmash": 1.5, r"\bgavel\b": 1})
DENIAL = re.compile(r"^(impossible|no+[.!]*|no\.\.\.|it can'?t be|that'?s not possible|you'?re lying)[.!]*$", re.I)

# --- humiliation / insult: the core revenge-drama itch ---------------------
INSULT = {
    r"\bbeggar\b": 2, r"\bworthless\b|\bnobody\b|\bnothing to offer\b": 2, r"\bplain(ness)?\b": 1.5,
    r"\bdisgrace": 1.5, r"\bkitchen maid|gutter rat|servant\b": 1.5, r"\bpenniless\b": 1.5,
    r"\bdiscarded\b|\bthrew away|thrown yourself away": 1.5, r"\bcrawl\b": 2, r"\byour place\b": 2,
    r"\blet her suffer\b": 3, r"\bwhat a (magnificent )?fall\b": 1.5, r"\bunworthy\b|low value": 1.5,
}
REACTION = r"gasp|shock|stunn|horrif|horror|disbelief|jaw|freez|aghast|recoil|stricken|bewild|petrified|terror"
TONE_HOT = r"scream|shout|furious|fury|hysteri|venom|cruel|menac|deadly|lethal|desperat|devastat|outrag|" \
           r"triumph|defiant|vicious|thunder|imperious|gloat|mock|scorn|brutal|frantic|panic|vindictive|accus|rage|" \
           r"heartbroken|betray|shatter|crushed|horrif|shock|disbelie|smug|callous|merciless|heartless|" \
           r"pleading|begging|crying out|gasping|piercing|seething|indignant|incredulous|disgust|harsh|cold\b"
TONE_SOFT = r"gentle|tender|warm|soft|peaceful|calm|quiet|relief|smil|laugh|melodic|reassur|affection"

# --- cliffhanger markers ----------------------------------------------------
INTERRUPT = r"pound|knock|footsteps|latch|door(s)? (burst|swing|fly|creak)|bursts? (in|open)|bang|" \
            r"someone is coming|bells? (begin|toll)|toll|hinges?"
WRITER_CLIFF = r"cliffhanger|sting|cut(s|ting)? (sharply |abruptly )?to black|freez(e|es) on|holds? on|" \
               r"crescendo|ominous|swells? (into|to)"
THREAT_FUTURE = r"\bwill (hang|die|burn|pay|watch)|\bnext\b|coming home|\btwo days\b|\bten days\b|" \
                r"before (he|the magistrate|that wedding)|\bgallows\b|\bhangs?\b|\btomorrow\b"
RESOLVE = r"\bthank you\b|\bsafe now\b|\bit is over\b|\bthen we will face it together|\bwe begin again\b|" \
          r"\bi accept\b(?!,)|\bfad(e|es|ing) out|\bfad(e|es|ing) to black|\bfade-out\b|peaceful|warm(th)?\b|sleep|" \
          r"into the (sunlight|light|daylight)|hand-in-hand|walk(s)? away together|triumphant (musical )?swell|" \
          r"unspoken bond|gaze deeply|tender"

CONNECTOR_START = re.compile(r"^(\.\.\.|…|and\b|but\b|so\b|then\b|or\b|because\b|which\b)", re.I)


def _lex(text: str, table: dict) -> tuple[float, list]:
    hits, score = [], 0.0
    t = text.lower()
    for pat, w in table.items():
        m = re.search(pat, t)
        if m:
            score += w
            hits.append(m.group(0))
    return score, hits


def premise_terms(title: str, loglines: list[str], top_n: int = 12, names: set | None = None) -> set:
    """Words that define *this* show's fantasy: title words + most frequent logline nouns."""
    stop = set("""a an the of to in and or for with her his he she him its it is was be by on at as from
        that this into their them they who whom whose after before while when where but not no only
        just one two all every over under out up down off than then so very most more less any each
        has have had are were been being do does did i you we our your my me us episode follows picks
        directly immediately following lady lord""".split())
    words = re.findall(r"[a-z']+", (title + " ").lower())
    terms = {w for w in words if w not in stop and len(w) > 3}
    cnt = Counter(w for l in loglines for w in re.findall(r"[a-z']+", l.lower())
                  if w not in stop and len(w) > 4)
    names = {n.lower() for n in (names or set())}
    names |= {n + "'s" for n in names}
    stop2 = {"about", "against", "inside", "through", "until", "beside", "aside", "someone", "together",
             "turns", "stands", "learns", "arrives", "returns", "unaware", "directly"}
    ranked = [w for w, _ in cnt.most_common() if w not in names and w not in stop2]
    terms |= set(ranked[:top_n])
    return {t for t in terms if t not in names}


def beat_signals(beat, premise: set) -> dict:
    """Return a dict of named sub-scores for one beat + the words that fired."""
    text = beat.text
    low = text.lower()
    tone = beat.tone.lower()
    phys, ph = _lex(text, PHYSICAL)
    rev, rh = _lex(text, REVEAL)
    ins, ih = _lex(text, INSULT)
    react = len(re.findall(REACTION, low + " " + tone))
    hot = len(re.findall(TONE_HOT, tone)) + (0.5 * len(re.findall(TONE_HOT, low)) if beat.kind != "dialogue" else 0)
    soft = len(re.findall(TONE_SOFT, tone + " " + (low if beat.kind != "dialogue" else "")))
    words = re.findall(r"[A-Za-z']+", text)
    nwords = len(words)

    s = {"physical": 0.0, "reveal": 0.0, "insult": 0.0, "reaction": 0.0, "heat": 0.0,
         "soft": float(soft), "question": 0.0, "punch": 0.0, "interrupt": 0.0,
         "writer_cliff": 0.0, "threat": 0.0, "resolve": 0.0, "premise": 0.0, "hits": []}

    if beat.kind == "dialogue":
        s["reveal"] = rev
        s["insult"] = ins
        s["physical"] = 0.4 * phys          # talking about violence < showing it
        s["heat"] = hot
        if text.rstrip().endswith("?"):
            s["question"] = 1.0
        if nwords <= 7 and re.search(r"[!?.]$|\.\.\.$", text.strip()):
            s["punch"] = 1.0 if text.strip().endswith(("!", "?")) else 0.6
        if re.search(r"(\.\.\.|—|-)$", text.strip()):
            s["question"] = max(s["question"], 0.6)   # cut-off line
        s["threat"] = len(re.findall(THREAT_FUTURE, low))
        if text.strip().endswith("!"):
            s["heat"] += 0.4
        if DENIAL.match(text.strip()):
            strong = not re.match(r"^no+[.!]*$", text.strip(), re.I)
            s["reaction"] += 2.0 if strong else 0.8   # "Impossible." is the classic last line; "No." less so
            s["denial"] = 1.0 if strong else 0.4
        s["hits"] = ph + rh + ih
    else:
        s["physical"] = phys
        s["reveal"] = 0.3 * rev
        s["reaction"] = react
        s["heat"] = hot
        s["interrupt"] = 1.0 if re.search(INTERRUPT, low) else 0.0
        s["writer_cliff"] = 1.0 if re.search(WRITER_CLIFF, low) else 0.0
        s["hits"] = ph + rh
    s["resolve"] = len(re.findall(RESOLVE, low)) + 0.3 * soft
    if s["writer_cliff"] and re.search(r"cliffhanger|sting|ominous", low):
        s["resolve"] = 0.0      # the writer told us this is the hanging beat
    s["premise"] = sum(1 for w in set(re.findall(r"[a-z']+", low)) if w in premise)
    return s


def hook_score(sig: dict) -> float:
    """How likely this beat stops a thumb in the first 3 seconds."""
    return (1.0 * sig["physical"] + 1.0 * sig["reveal"] + 0.9 * sig["insult"]
            + 0.6 * sig["reaction"] + 0.35 * sig["heat"] + 0.8 * sig["punch"]
            + 0.5 * sig["question"] + 0.6 * sig["interrupt"] - 0.4 * sig["soft"])


def cliff_score(sig: dict) -> float:
    """How unresolved the moment feels if we cut right after it."""
    return (1.6 * sig["question"] + 1.2 * sig["interrupt"] + 1.3 * sig["writer_cliff"]
            + 0.9 * sig["threat"] + 0.5 * sig["reveal"] + 0.5 * sig["physical"]
            + 0.4 * sig["reaction"] + 0.3 * sig["heat"] + 0.4 * sig["punch"]
            + 1.5 * sig.get("denial", 0) - 1.0 * sig["resolve"])


def intensity(sig: dict) -> float:
    return max(0.0, 0.6 * sig["physical"] + 0.6 * sig["reveal"] + 0.5 * sig["insult"]
               + 0.4 * sig["reaction"] + 0.3 * sig["heat"] + 0.3 * sig["punch"]
               + 0.3 * sig["question"] - 0.3 * sig["soft"])


def starts_mid_thought(text: str) -> bool:
    return bool(CONNECTOR_START.match(text.strip().strip('"')))


# --- ad-safety: platforms (Meta/TikTok/YouTube) restrict some ad creative --
SAFETY = {
    "sexual": r"\bnaked\b|\bbare (skin|chest)\b|\bshirtless\b|\bin this bed\b|\bpassionate embrace\b|"
              r"\bforbidden entanglement\b|\blies flat on his back, breathless\b|\bintimate embrace upon the bed\b",
    "graphic_injury": r"inches from her (right )?eye|\bmillimet|\bsearing\b|through flesh|\bshredded\b|"
                      r"bloody welt|\blacerat",
    "child_in_peril": r"\bboy is burning\b|\bis dying\b.*\b(boy|brother|son)\b|\bonly seven\b|\bfeverish boy\b",
}


def safety_flags(texts: list[str]) -> list[str]:
    """Flags for ad-policy review. Scans spoken/seen text only, never tone tags ("Passionate")."""
    joined = " ".join(texts).lower()
    return [k for k, pat in SAFETY.items() if re.search(pat, joined)]
