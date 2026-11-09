# promo-finder

Reads a timestamped micro-drama screenplay (one `.txt` per episode) and returns the promos an editor should
cut: **20-60 s clips that open on a hook in the first 3 s, end on a cliffhanger, and make sense to a stranger**.
For each promo it gives every segment's episode + start/end, the hook, the cliffhanger, one line on why it works,
a posting order with reasons, and automatic quality checks. Built for Kuku's AI Generalist assignment, Problem 1.

```
python -m promofinder run --input "<show>-script.zip" --out outputs/
```

Python 3.9+, **standard library only** (nothing to install). The full 50-episode pack runs in about 1 s with no model,
and in about 11 calls with one.

## What you get (`outputs/`)
| file | for |
|---|---|
| `shortlist.md` | the top 15 in posting order: cuts, hook, cliffhanger, why it works, why it's at that position, transcript |
| `promos.csv` | every promo, one row (open in Sheets) |
| `edit_decision_list.csv` | one row per segment: what the editor actually cuts |
| `report.html` | browsable, filterable view of all promos |
| `promos.json` | everything, incl. score breakdowns |
| `qa_report.md` | automatic checks (+ evaluation if you pass `--gold`) |
| `run_log.txt` | command, timings, model calls, tokens, cost estimate |

## How it works
```
screenplay .txt ──► parse ──► score every beat ──► generate candidates ──► de-duplicate ──► [LLM editor] ──► rank ──► QA ──► outputs
                  (code)      (rules, cheap,        single / stitched /      (code)          (optional,      (code)   (code)
                              explainable)          cross-episode / trailer                  swappable)
```
1. **Parse** (`parse.py`): every CAMERA / ACTION / SOUND FX / dialogue block becomes a timed beat. Camera shots
   become legal cut points.
2. **Score beats** (`signals.py`): hook signals (physical violence, reveals, humiliation, shock reactions, punchy
   lines, tone tags like *Cold, Callous*) and cliffhanger signals (unanswered questions, interruptions, threats,
   flat denials, the writer's own "cliffhanger" cues, end-of-episode position), minus resolution signals.
   A question only counts as a cliffhanger if something is at stake in the 10 s before it.
3. **Candidates** (`candidates.py`):
   - *single*: one continuous stretch, snapped so it never clips a line and starts on a shot boundary
   - *stitched*: a scene too long/slow to post raw, compressed to hook + hottest exchanges + cliff (2-4 cuts)
   - *cross-episode*: hook late in episode N, cliff in N+1, only when N+1's continuity note says it picks up
     directly; the next episode's recap lines are detected and skipped
   - *trailer*: an early-episodes "what is this show" montage and a full-arc one
4. **De-duplicate**: overlapping cuts, or cuts that open on the same shot, count as one promo. Max 3 per episode.
5. **LLM editor (optional, `judge.py`)**: a model reviews each episode's shortlist like a senior promo editor:
   keep or reject, tighten boundaries, propose one missed promo, write the hook/cliff/why lines, flag ad-policy risks.
   It **only ever returns beat ids**; code turns ids into timestamps and re-runs every QA check. If an edit breaks
   a rule, the rules version is kept. A final call orders the top 20 for posting.
6. **Rank** (`pipeline.py`): quality, nudged toward early footage (a new viewer lands on episode 1 and nothing is
   spoiled) and the title premise, with ad-policy risks flagged and demoted, not removed. No episode is used twice
   in the top 15.
7. **QA** (`qa.py`): 20-60 s; hook beat in the first 3 s; no clipped line at any cut; segments in order and in bounds;
   no dead air at the open; ending isn't a resolution; doesn't open mid-sentence; no sub-2.5 s jump cuts;
   ad-policy flags (sexual content, graphic injury, child in danger).

## Swap the model (flag, not code)
```
--provider none                                   # default: rules only, offline, $0
--provider gemini    [--model gemini-2.5-flash]   # GEMINI_API_KEY (free tier works; model ids change, check AI Studio)
--provider openai    [--model gpt-4o-mini]        # OPENAI_API_KEY
--provider openai --base-url https://api.groq.com/openai/v1 --model <id>     # any OpenAI-compatible API
--provider ollama --model llama3.1                # local, free (http://localhost:11434)
--provider anthropic [--model claude-sonnet-4-5]  # ANTHROPIC_API_KEY
--batch 5    # episodes per call (5 -> ~11 calls/show; 1 = most careful)
--rpm 10     # pace calls for free-tier rate limits; responses are cached in .cache/ so re-runs are free
```
The prompt lives in `prompts/judge_system.md`, so it can be edited without touching code.

## Evaluate
```
python -m promofinder run --input ... --out ... --gold eval/gold_moments.csv
python -m unittest discover -s tests -v                      # synthetic screenplay, no input pack needed
python tests/mock_llm_server.py 8765 &                        # fake model, to test the LLM path offline
python -m promofinder run --input ... --out /tmp/x --provider openai --base-url http://127.0.0.1:8765/v1 --api-key x
```
See `eval/README.md` for the gold set, the matching rule and caveats.

## Input format it expects (and tolerates)
`EPISODE NUMBER / EPISODE LOGLINE / CONTINUITY NOTES` headers, `SCENE nn` blocks, and `**LABEL (Tone):**` +
`Start: (MM:SS.cc) | End: (MM:SS.cc)` + text. It also accepts HH:MM:SS timestamps, CRLF files, missing scene headers,
unknown labels (treated as speakers), a single file, a folder (searched recursively) or a `.zip`.

## Limits
- It reads **text**, not video: timestamps are the screenplay's. If the rendered episode drifts from the script,
  cuts need re-aligning (e.g. match subtitles/ASR to the dialogue lines).
- Rules see words, not pictures: a talky scene full of "poison" and "witchcraft" can outscore a silent, devastating look.
- Compression can drop the setup line of a payoff. The LLM pass is designed to catch this; rules alone don't.
- Ad-policy flags are keyword-based, so they're for review, not decisions.

The input pack is Kuku's confidential content; `.gitignore` keeps scripts, outputs and the gold set out of git.
