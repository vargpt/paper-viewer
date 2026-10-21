# Multi-agent paper explainer

Started as a copy of `baseline/`: same input (a paper), same output (one self-contained interactive
HTML page, plus a JSON with run metadata), same file naming. The single LLM call is replaced by a
team of agents:

```
paper
  │
  ▼
lead editor ─── reads the paper, picks 3 paper-specific experts, plans the page
  │
  ▼
experts, in parallel ─── write their sections: explanations, interactive visuals, toy examples
  │
  ▼
┌─ review ─────────────────────────────────────────────────────────┐
│  simulated readers: an 8-year-old … a professor … a 70-year-old   │
│  fact-checker: every section against the paper                    │  repeats for
│  experts review each other's sections; browser checks             │  --rounds
│        │                                                          │
│        ▼                                                          │
│  lead editor writes tickets ──▶ experts revise their sections     │
└───────────────────────────────────────────────────────────────────┘
  │
  ▼
final fact-check + fixes ──▶ one self-contained HTML page
```

1. **Plan.** The lead editor reads the paper (text, page images, figure crops), writes down the
   problem, key idea, method, results with exact numbers and limitations, and **decides which experts
   this paper needs** (3–5, specific to the paper, e.g. "contrastive learning for medical images
   expert"). It designs the page as a story of 5–8 sections, gives each section one owner, and writes
   the header (headline, a kid / general / expert summary, key numbers) and the glossary.
2. **Build.** The experts work in parallel. Each writes its sections as HTML fragments with
   explanations, interactive visualisations and toy examples, using a shared design system
   (`page.py`). While working they can render a section in a real browser (`check_section`: errors,
   phone overflow, screenshots they look at) and crop figures out of the paper (`crop_figure`).
3. **Review.** The assembled page is read by simulated readers: Maya (8, with her dad), Jordan (15),
   Priya (25, graduate from another field), David (42, a practitioner who might use the work), Prof.
   Rossi (a professor in the field) and Margaret (70, no AI background). Each looks at screenshots of
   the page and its text, scores it, explains the paper back in their own words, and says what
   confused them. At the same time a fact-checker audits every section (and the header) against the
   paper, and every expert reviews the other experts' sections from its own expertise.
4. **Edit.** The lead editor turns all of that into prioritised tickets per section (fact-check
   issues are always "must"), resolves conflicts between readers with layering (kid box *and*
   details for experts, instead of compromising), and rewrites the header/glossary if needed. The
   experts revise their sections, with the team's notes to keep terminology consistent.
   Steps 3–4 repeat for `--rounds` rounds, or stop early when the editor is satisfied and nothing
   factual or technical is left.
5. **Verify.** A last fact-check and browser check of the finished page; owners fix what it finds.

The readers map onto the eval rubric's age brackets (`eval/prompt.md`: 5, 15, 25, 35–45, 55–65), and
the fact-checker uses the rubric's definition of a hallucination.

## Setup

```bash
pip install -r multiagent/requirements.txt     # or the repo's requirements.txt
playwright install chromium                     # optional; an installed Google Chrome also works
```

**Auth: OpenRouter (default).** Agents run through the
[Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk), which drives Claude Code. By default
Claude Code is pointed at OpenRouter's Anthropic-compatible API with `OPENROUTER_API_KEY` (or the
baseline's `LLM_API_KEY`) from `paper-viewer/.env`, the same provider and model naming as the baseline
(`anthropic/claude-opus-5.5`), so costs show up on the OpenRouter account. Other options:
`--auth subscription` uses the Claude account you're logged into in Claude Code (Pro/Max usage limits,
model `claude-opus-5-5`); `--auth api-key` uses `ANTHROPIC_API_KEY`.

## Run

```bash
python multiagent/multiagent.py eval/papers/Konate_SphereEdit_WACV2026.pdf             # full run
python multiagent/multiagent.py eval/papers/2411.00609.pdf --quick                      # cheaper: 1 round, 3 readers, ≤3 experts
python multiagent/multiagent.py eval/papers/2411.00609.pdf --runs 3                     # best-of-3 for the eval
python multiagent/generate_missing.py --dry-run                                         # which eval papers have no output yet
python multiagent/generate_missing.py                                                   # make them, one paper at a time
```

Every step is checkpointed in the run's workspace. If a run is interrupted, or stops because the
usage window or credit runs out, **re-run the same command and it resumes**. `--fresh` starts
the run over.

| option | default | |
|---|---|---|
| `--model` | `anthropic/claude-opus-5.5` (`claude-opus-5-5` without OpenRouter) | editor, experts, fact-checker |
| `--reader-model` | Sonnet 5.5 (`anthropic/claude-sonnet-5.5`) | simulated readers; cheaper than Opus, they don't write anything |
| `--effort` | `high` | reasoning effort for every agent |
| `--rounds` | `1` | review-and-revise rounds |
| `--readers` | `kid,teen,professor,senior` | any of `kid,teen,grad,pro,professor,senior`, or `all` |
| `--max-experts` | `3` | the editor picks up to this many (at least 3, or this many if fewer) |
| `--no-peer-review` | off | skip the experts' reviews of each other |
| `--parallel` | `4` | agents running at once |
| `--quick` | | `kid,grad,professor` readers, ≤6 sections, no peer review |
| `--out` / `--work` | `multiagent/outputs` / `multiagent/work` | final pages / run workspaces |

**Cost.** A default run (3 experts, 4 readers, 1 round) is about 30 agent sessions and takes
about 30–40 minutes. The JSON next to each page reports the cost as Claude Code estimates it at
Anthropic list prices (`cost_usd_api_equivalent`) and the tokens; OpenRouter's dashboard has the
exact bill.

## Outputs

`multiagent/outputs/<paper>__<model>__run<i>.html` is the final page (figures inlined; Plotly and KaTeX
come from CDNs, as in the baseline). The `.json` next to it has the experts and their sections, reader
scores and fact-check counts per round, time, sessions, tokens and cost.

Everything else is in the workspace, `multiagent/work/<paper>__<model>__run<i>/` (git-ignored):

| | |
|---|---|
| `plan.json` | the editor's plan: paper summary, experts, sections, header, glossary |
| `sections/<id>.html` | each section as its expert wrote it |
| `page-v1.html`, `page-v2.html`, … | the page before each review round; `final.html` the result |
| `rounds/<n>/` | `reader-*.json`, `audit-*.json`, `peer-*.json`, `qa.json`, `edit.json` (the tickets), `screens/` |
| `final/` | the last fact-check and browser check |
| `logs/<agent>.md` | every agent's prompt and transcript (tool calls, results, answer); `usage.jsonl` per session |
| `paper.md`, `pages/`, `figures/` | the extracted paper the agents read |

## Changing the framework

- `prompts.py`: every agent's instructions, the six reader personas (`PERSONAS`), the style guide the
  experts follow, and the JSON schemas of each agent's answer. This is the place to tune behaviour.
- `page.py`: the page shell and design system (CSS components, the `PV` chart helpers, header,
  glossary, footer), static checks, and the text version of the page that readers and fact-checkers get.
- `multiagent.py`: the pipeline (plan → build → review/edit rounds → verify).
- `agents.py`: how one agent runs (Agent SDK options, auth, write permissions, logs, retries).
- `paper.py` (text, page images, figure crops), `browser.py` (Playwright checks), `tools.py`
  (`check_section`, `crop_figure`).
- `test_pipeline.py` runs the whole pipeline with stub agents (no model calls, no usage) and checks
  the orchestration, checkpointing and resume. Run it after changing `multiagent.py`.

## Guard rails

Agents run Claude Code in `--restricted` mode with only the tools they need: no shell, no web access,
file tools confined to the run's workspace, and a hook that lets each expert write only its own section
files (and keeps every agent out of `logs/`). Your user/project Claude Code settings, CLAUDE.md files
and MCP servers are not loaded.

## Known limitations

- Figure crops are automatic and sometimes miss a figure or include a sliver of text; experts can
  re-crop with `crop_figure`. JATS XML papers have no figures at all, so their visuals are all built.
- Readers judge screenshots and text; they can't operate the widgets.
- The fact-checker and the readers are the same model family as the authors, so treat their scores as
  a guide, and keep the human and multi-judge evaluation from `SPEC.md`.
