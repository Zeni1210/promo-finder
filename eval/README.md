# How the evaluation works

## Gold set (`gold_moments.csv`)
35 promo moments (20 tier A = "would post this week", 15 tier B = "solid"), marked while reading all 50
episodes end to end, **before** the scoring rules were written. Hash and time when frozen are in
`gold_frozen_at.txt` (`sha256 68b3d797…`). Each row is an episode and an approximate span, not exact cut points.

## Matching rule
A tool promo "finds" a gold moment if it shares ≥10 s of footage with it in that episode, or ≥50 % of the
shorter span. *Changed once, during development:* the first rule (≥50 % of the shorter span only) marked
the Episode 1 altar cut as a miss because the tool opened 24 s later, on a stronger hook ("Why are you holding
her hand?"). Same moment, different cut points, so I loosened it. Every metric reported uses the final rule.

## Metrics
- **recall**: share of gold moments that appear anywhere in the tool's output (all 150 promos)
- **recall_A@k**: share of tier-A moments that appear in the tool's top k posting order
- **precision@k**: share of the top k that match *some* gold moment (a lower bound, since gold isn't exhaustive)
- **automatic QA pass rate** (from `qa.py`)
- plus a **manual review** of the top 15 (`raw_outputs/rules_only/manual_review_top15.md`), graded hook / cliff /
  standalone on 0-2 without looking at scores first

## Honest caveats
- The same person (with the same AI assistant) wrote the gold set and tuned the rules. I tuned only general
  rules (lexicons, boundary logic, de-duplication), never episode-specific ones, but this is the biggest weakness.
- Results are split into episodes 1-25 and 26-50. I looked at both halves while tuning, so 26-50 is **not** a clean
  hold-out. The real test is a new show, which is what the interview run will be.
- The gold set is one person's taste. In production it should be replaced by the metric that matters:
  promo view-through, click-through and installs per promo.

## Final numbers (rules only, full pack)
| metric | value |
|---|---|
| gold moments found anywhere | 35/35 (100 %) |
| tier-A moments in top 15 | 10/20 (50 %) |
| tier-A moments in top 30 | 13/20 (65 %) |
| precision@15 | 13/15 (87 %) |
| automatic QA pass | 134/150 (89 %) |
| manual review: usable for acquisition (as is or ≤5 s trim) | 9/15 |
