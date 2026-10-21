# Baseline vs multi-agent: evaluation comparison

Two papers, one run each. Same rubric (`eval/prompt.md`) and script (`eval/evaluate_baselines.py`).

- **Baseline** = single LLM call, no tools (`baseline/baseline.py`); results in `eval/results/`.
- **Multi-agent** = pages in `multiagent/outputs/before-visuals/` (generated before the visual-first redesign).
- **Judge: `anthropic/claude-sonnet-5.5` for both**, so the comparison is like for like. Multi-agent results with this judge are in `sonnet-judge/`; an earlier scoring by `claude-opus-5.5` is kept in this folder for reference (last section).
- Pages were scored with inline images removed (the full HTML was too large to send), so the judge saw text and structure, not pictures.
- Two papers and one run each: an early signal, not a result.

## 2404.14414 (Removing reflections)

| Metric | Baseline | Multi-agent | Change |
|---|---|---|---|
| Hallucinations (major) | 0 (0) | 1 (0) | +1 |
| Consistency /10 | 9 | 8 | -1 |
| Missing key points | 4 | 4 | |
| Quality, age 5 /10 | 2 | 4 | +2 |
| Quality, age 15 /10 | 5 | 7 | +2 |
| Quality, age 25 /10 | 9 | 9 | +0 |
| Quality, age 35 /10 | 9 | 9 | +0 |
| Quality, age 45 /10 | 8 | 8 | +0 |
| Quality, age 55 /10 | 6 | 7 | +1 |
| Quality, age 65 /10 | 5 | 7 | +2 |
| **Quality mean /10** | **6.29** | **7.29** | **+1.00** |

Multi-agent hallucinations flagged by the judge:
- (minor) "8-bit models barely change the photo; retrained ones leave blur; the paper's models peel the reflection off as its own layer." — The blanket statement, repeated in the per-method captions (e.g. Zhang 8-bit "clean layer still looks much like the captured photo"), oversimplifies and partly contradicts the paper's description of color distortion and blue-content removal

Sources: `eval/results/2404.14414__2404.14414__anthropic_claude-opus-5.5__run1.json` and `multiagent/outputs/eval/sonnet-judge/2404.14414__2404.14414__anthropic_claude-opus-5.5__run1.json`.

## Konate_SphereEdit_WACV2026 (SphereEdit)

| Metric | Baseline | Multi-agent | Change |
|---|---|---|---|
| Hallucinations (major) | 0 (0) | 0 (0) | +0 |
| Consistency /10 | 8 | 9 | +1 |
| Missing key points | 4 | 4 | |
| Quality, age 5 /10 | 2 | 4 | +2 |
| Quality, age 15 /10 | 5 | 7 | +2 |
| Quality, age 25 /10 | 8 | 9 | +1 |
| Quality, age 35 /10 | 8 | 9 | +1 |
| Quality, age 45 /10 | 7 | 8 | +1 |
| Quality, age 55 /10 | 5 | 7 | +2 |
| Quality, age 65 /10 | 5 | 7 | +2 |
| **Quality mean /10** | **5.71** | **7.29** | **+1.58** |

Multi-agent hallucinations flagged by the judge:
- none

Sources: `eval/results/Konate_SphereEdit_WACV2026__Konate_SphereEdit_WACV2026__anthropic_claude-opus-5.5__run1.json` and `multiagent/outputs/eval/sonnet-judge/Konate_SphereEdit_WACV2026__Konate_SphereEdit_WACV2026__anthropic_claude-opus-5.5__run1.json`.

## Pairwise comparison (judge sees both pages side by side)

Script: `eval/pairwise.py`; results: `multiagent/outputs/eval/pairwise/`. Judge: `anthropic/claude-sonnet-5.5`. For each reader age the judge picks the better page for that reader. Every pair is judged twice with the page order swapped, and a page only wins an age group if both orders agree (otherwise tie). Images were removed from the pages.

| Paper | Overall | Ages won by multi-agent | Ages won by baseline | Ties | Orders agreed |
|---|---|---|---|---|---|
| 2404.14414 (Removing reflections) | candidate | 7/7 | 0/7 | 0/7 | 8/8 |
| SphereEdit | candidate | 7/7 | 0/7 | 0/7 | 8/8 |

Typical reasons the judge gave: the multi-agent page has a kid-level box and analogies, layered detail (short summary first, depth in collapsible blocks), a glossary, text-size controls and toy demos. The baseline is denser, jargon-heavy, in a dark small-font theme.

Unsupported claims the judge reported (both orders combined): 2404.14414 baseline 6, multi-agent 6; SphereEdit baseline 4, multi-agent 3. Several of the multi-agent ones are the page's own derivations or readings of figures (for example a Fresnel calculation the paper does not state), so the extra interpretation in these pages is worth watching.

Why this is more informative than the absolute scores: the rubric scores cluster tightly (both papers got identical age scores), but a head-to-head comparison separates the pages. Caveats: two papers, one run each; an LLM judge may favor richer, longer pages; images were not seen.

## For reference: the same pages scored by Opus 5.5

| Paper | Quality mean (Opus judge, multi-agent) | Quality mean (Sonnet judge, multi-agent) |
|---|---|---|
| 2404.14414 (Removing reflections) | 7.14 | 7.29 |
| Konate_SphereEdit_WACV2026 (SphereEdit) | 7.14 | 7.29 |
