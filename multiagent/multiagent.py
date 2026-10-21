"""Multi-agent explainer: a lead editor, paper-specific experts, simulated readers and a fact-checker.

Same input and output as baseline/baseline.py (a paper in, one self-contained interactive HTML page
out), but made by a team of agents instead of a single LLM call:

  1. plan    the lead editor reads the paper, decides which experts it needs, and designs the page
  2. build   each expert writes its sections (explanations, interactive visuals, toy examples),
             checking them in a real browser as it goes
  3. review  simulated readers (a child, a teen, a graduate, a professional, a professor, a retiree)
             read the page; a fact-checker audits every section against the paper; the experts
             review each other's sections
  4. edit    the lead editor turns all feedback into tickets and the experts revise their sections
             (3 and 4 repeat for --rounds rounds, or until the editor is satisfied)
  5. verify  a last fact-check and browser check of the finished page; owners fix what it finds

Agents run through the Claude Agent SDK, by default on OpenRouter with the key in paper-viewer/.env
(the same provider as the baseline); --auth subscription uses your Claude login instead. Every step is
saved in the run's workspace, so re-running the same command resumes
after an interruption or a usage limit.

    python multiagent/multiagent.py paper.pdf
    python multiagent/multiagent.py paper.pdf --quick
    python multiagent/multiagent.py paper.pdf --rounds 3 --readers kid,teen,professor --runs 3
"""
import argparse
import asyncio
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from dotenv import load_dotenv

import prompts as P
from agents import Team, UsageLimit, use_auth
from browser import Browser
from page import (assemble, concept_check, fill, inline_images, main_words, page_text, render_glossary, render_hero, section_texts,
                  static_check)
from paper import Paper, load_paper
from tools import expert_tools

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_MODEL = {"openrouter": "anthropic/claude-opus-5.5", "subscription": "claude-opus-5-5", "api-key": "claude-opus-5-5"}
LOOK_TOOLS = ["Read", "Glob", "Grep"]
EDIT_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep"]
HERO_FIELDS = list(P.HERO["properties"])
MAX_PLAYS = 12  # widgets operated per page check
READER_MODEL = {"openrouter": "anthropic/claude-sonnet-5.5", "subscription": "claude-sonnet-5-5", "api-key": "claude-sonnet-5-5"}
DEFAULT_READERS = ["kid", "teen", "professor", "senior"]


@dataclass
class Config:
    model: str = DEFAULT_MODEL["openrouter"]
    reader_model: str | None = None
    effort: str = "high"
    expert_effort: str = "medium"  # the experts' build, revise, fix and peer-review sessions
    rounds: int = 1
    readers: list[str] = field(default_factory=lambda: list(DEFAULT_READERS))
    max_experts: int = 3
    max_sections: int = 5
    peer_review: bool = True
    parallel: int = 4
    auth: str = "openrouter"
    expert_turns: int = 80

    @property
    def personas(self) -> list[P.Persona]:
        return [p for p in P.PERSONAS if p.id in self.readers]


class State:
    """Steps already done (state.json), so a re-run resumes where the last one stopped."""

    def __init__(self, path: Path):
        self.path = path
        self.done = json.loads(path.read_text()) if path.exists() else {}

    def __contains__(self, key: str) -> bool:
        return key in self.done

    def mark(self, key: str, value=True) -> None:
        self.done[key] = value
        self.path.write_text(jdump(self.done))


def jdump(data) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False)


async def cached(path: Path, make):
    """Load a step's JSON result if it exists, else compute it (make is a zero-argument coroutine function)."""
    if path.exists():
        return json.loads(path.read_text())
    data = await make()
    if data is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(jdump(data))
    return data


async def guarded(team: Team, name: str, job):
    """One agent failing shouldn't sink the run (a used-up usage window should)."""
    try:
        return await job
    except UsageLimit:
        raise
    except Exception as e:
        team.say(name, f"gave up: {type(e).__name__}: {str(e)[:240]}")
        return None


async def run_all(jobs: list):
    tasks = [asyncio.ensure_future(j) for j in jobs]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


# ---------------------------------------------------------------- the team's shared context

def lead_system(paper: Paper) -> str:
    return fill(P.LEAD_SYSTEM, style=P.STYLE_GUIDE, paper=paper.text)


def expert_system(paper: Paper, ws: Path) -> str:
    return fill(P.EXPERT_SYSTEM, style=P.STYLE_GUIDE, paper=paper.text, ws=str(ws),
                files=P.PDF_FILES if paper.kind == "pdf" else P.XML_FILES)


def audit_system(paper: Paper) -> str:
    return fill(P.AUDIT_SYSTEM, paper=paper.text)


def owned(plan: dict, expert: dict) -> list[dict]:
    return [s for s in plan["sections"] if s["owner"] == expert["id"]]


def own_list(plan: dict, expert: dict) -> str:
    return ", ".join(f"`{s['id']}` ({s['title']})" for s in owned(plan, expert))


def section_files(ws: Path, plan: dict, expert: dict) -> list[Path]:
    return [ws / "sections" / f"{s['id']}.html" for s in owned(plan, expert)]


def outline(plan: dict) -> str:
    names = {e["id"]: e["name"] for e in plan["experts"]}
    story = plan.get("story") or {}
    head = [f"The point of the page: {story['point']}"] if story.get("point") else []
    return "\n".join(head + [f"{i}. `{s['id']}`: {s['title']} (by {names[s['owner']]}). {s['goal']}"
                              + (f" Not here: {s['avoid']}" if s.get("avoid") else "")
                              for i, s in enumerate(plan["sections"], 1)])


def assignment(plan: dict, expert: dict, paper: Paper) -> str:
    figures = {f["id"]: f for f in paper.figures}
    parts = []
    for s in owned(plan, expert):
        lines = [f"### `{s['id']}`: {s['title']}", f"Goal: {s['goal']}", "Key points:", *[f"- {k}" for k in s["key_points"]],
                 *([f"Don't repeat or cover (another section does): {s['avoid']}"] if s.get("avoid") else []),
                 f"Where in the paper: {s['paper_refs']}",
                 f"Length: at most {P.SECTION_WORDS} words of main text, outside details blocks."]
        if s.get("concepts"):
            lines.append("Concepts, each with its own visual (mark its container data-concept=\"<id>\"; you may build a "
                         "better visual for a concept than the one planned):")
            lines += [f"- `{c['id']}`: {c['concept']} Visual: {c['visual']}. The reader: {c['interaction']} Default state: "
                      f"{c['default']} Built on: {c['built_on']}" for c in s["concepts"]]
        else:  # plans written before concepts existed
            lines += ["Visual ideas (suggestions; do better if you can):", *[f"- {v}" for v in s.get("visuals", [])]]
        if s["figures"]:
            lines += ["Paper figures you could use:", *[f"- {figures[f]['file']}: {figures[f]['caption'][:240]}" for f in s["figures"]]]
        lines.append(f"Write it to: sections/{s['id']}.html")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def figure_list(paper: Paper) -> str:
    if paper.kind != "pdf":
        return "(none: this paper came as XML, so every visual has to be built)"
    if not paper.figures:
        return "(none found automatically; page images are in pages/, and experts can crop figures from them)"
    return "\n".join(f"- {f['id']} (page {f['page']}, {f['file']}): {f['caption'][:300]}" for f in paper.figures)


def reader_names(cfg: Config) -> list[str]:
    return [p.who for p in cfg.personas]


def tools_for(team: Team, plan: dict, paper: Paper, browser: Browser, cfg: Config) -> dict:
    return expert_tools(team.ws, plan, paper, browser, readers=reader_names(cfg), rounds=cfg.rounds)


def save_plan(ws: Path, plan: dict) -> None:
    (ws / "plan.json").write_text(jdump(plan))


def write_draft(ws: Path, plan: dict, cfg: Config, label: str, rounds: int) -> Path:
    html = assemble(ws, plan, readers=reader_names(cfg), rounds=rounds)
    (ws / "page.html").write_text(html)
    path = ws / f"page-{label}.html"
    path.write_text(html)
    return path


# ---------------------------------------------------------------- 1. plan

def _slug(text: str, taken: set) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:40] or "part"
    if s in ("hero", "top", "main", "glossary"):
        s += "-section"
    base, i = s, 2
    while s in taken:
        s, i = f"{base}-{i}", i + 1
    taken.add(s)
    return s


def normalize_plan(plan: dict, paper: Paper, cfg: Config) -> dict:
    """Clean ids, enforce one known owner per section, and drop experts without sections."""
    taken, renamed = set(), {}
    experts = plan["experts"][: cfg.max_experts]
    for e in experts:
        new = _slug(e["id"] or e["name"], taken)
        renamed[e["id"]] = renamed[e["name"]] = e["id"] = new
    known_figures = {f["id"] for f in paper.figures}
    taken = set()
    for s in plan["sections"]:
        s["id"] = _slug(s["id"] or s["title"], taken)
        s["owner"] = renamed.get(s["owner"], s["owner"])
        if s["owner"] not in {e["id"] for e in experts}:
            load = {e["id"]: sum(x["owner"] == e["id"] for x in plan["sections"]) for e in experts}
            s["owner"] = min(load, key=load.get)
        s["figures"] = [f for f in s["figures"] if f in known_figures]
    concept_ids = set()
    for s in plan["sections"]:
        s["concepts"] = [dict(c) for c in s.get("concepts") or []]
        for c in s["concepts"]:
            c["id"] = _slug(c["id"] or c["concept"], concept_ids)
    plan["experts"] = [e for e in experts if any(s["owner"] == e["id"] for s in plan["sections"])]
    return plan


async def plan_page(team: Team, paper: Paper, cfg: Config) -> dict:
    path = team.ws / "plan.json"
    if path.exists():
        return json.loads(path.read_text())
    look = (" Page images are in pages/ and figure crops in figures/: look at the important figures (Read the image"
            " files)." if paper.kind == "pdf" else "")
    prompt = fill(P.PLAN_PROMPT, look=look, min_experts=str(min(3, cfg.max_experts)), max_experts=str(cfg.max_experts),
                  min_sections=str(min(3, cfg.max_sections)), max_sections=str(cfg.max_sections), figures=figure_list(paper),
                  words=str(P.SECTION_WORDS), page_words=str(P.PAGE_WORDS))
    run = await team.run("lead-plan", prompt, system=lead_system(paper), tools=LOOK_TOOLS if paper.kind == "pdf" else [],
                         schema=P.PLAN_SCHEMA, max_turns=40)
    plan = normalize_plan(run.data, paper, cfg)
    save_plan(team.ws, plan)
    for e in plan["experts"]:
        team.say("lead-plan", f"expert: {e['name']} -> {', '.join(s['id'] for s in owned(plan, e))}")
    team.say("lead-plan", f"{sum(len(s.get('concepts') or []) for s in plan['sections'])} concepts, each with a visual: "
             + ", ".join(f"{c['id']} ({c['visual'][:30]})" for s in plan["sections"] for c in s.get("concepts") or []))
    return plan


# ---------------------------------------------------------------- 2. build

async def build(team: Team, browser: Browser, paper: Paper, plan: dict, cfg: Config, state: State) -> None:
    async def one(expert: dict):
        key = f"build:{expert['id']}"
        if key in state:
            return
        name = f"expert-{expert['id']}-build"
        prompt = fill(P.EXPERT_BUILD_PROMPT, name=expert["name"], expertise=expert["expertise"], brief=expert["brief"],
                      outline=outline(plan), notes=plan["notes"], assignment=assignment(plan, expert, paper))
        for attempt in range(2):
            run = await team.run(name, prompt, system=expert_system(paper, team.ws), tools=EDIT_TOOLS,
                                 writable=section_files(team.ws, plan, expert), mcp=tools_for(team, plan, paper, browser, cfg),
                                 schema=P.SUMMARY_SCHEMA, need_data=False, effort=cfg.expert_effort,
                                 max_turns=cfg.expert_turns)
            missing = [f.name for f in section_files(team.ws, plan, expert) if not f.exists()]
            if not missing:
                state.mark(key, run.data)
                return
            prompt += f"\n\nA previous attempt ended without writing {', '.join(missing)}. Write every one of your sections."
        raise RuntimeError(f"no {', '.join(missing)} after two attempts")

    await run_all([guarded(team, f"expert-{e['id']}-build", one(e)) for e in plan["experts"]])


# ---------------------------------------------------------------- 3. review

async def technical_qa(ws: Path, plan: dict, browser: Browser, draft: Path, shots: Path | None) -> dict:
    """Static checks of every section, then the assembled page in a browser (desktop and phone)."""
    problems: dict[str, list[str]] = {}
    words: dict[str, int] = {}
    for s in plan["sections"]:
        f = ws / "sections" / f"{s['id']}.html"
        found = static_check(f.read_text(), s["id"], ws) + concept_check(f.read_text(), s) if f.exists() else ["The section file is missing."]
        if found:
            problems[s["id"]] = found
        words[s["id"]] = main_words(f.read_text()) if f.exists() else 0
    # operate every widget too (dead widgets are problems); readers see the widgets after being operated
    rendered = await browser.render(draft, shots=shots or ws / "screenshots" / "qa", prefix="page",
                                    max_shots=16 if shots else 0, max_plays=MAX_PLAYS)
    screens, plays = [], []
    if rendered:
        for where, found in rendered.problems.items():
            problems.setdefault(where, []).extend(found)
        screens = [str(p.relative_to(ws)) for p in rendered.shots]
        plays = [[str(p.relative_to(ws)), what] for p, what in rendered.plays] if shots else []
    return {"problems": problems, "screens": screens, "plays": plays, "words": words}


def audit_parts(ws: Path, plan: dict, page_html: str) -> list[tuple[str, str, str, str]]:
    """(id, description, html, text) for the header + glossary and for every section."""
    texts = section_texts(page_html)
    header = render_hero(plan) + render_glossary(plan)
    parts = [("hero", "the page's header (headline, summaries, key numbers) and its glossary", header, page_text(header))]
    for s in plan["sections"]:
        f = ws / "sections" / f"{s['id']}.html"
        if f.exists():
            parts.append((s["id"], f"the section `{s['id']}` (\"{s['title']}\") of the explainer page", f.read_text(),
                          texts.get(s["id"], "")))
    return parts


async def audit(team: Team, paper: Paper, part: tuple, tag: str) -> dict:
    sid, what, html, text = part
    run = await team.run(f"audit-{sid}-{tag}", fill(P.AUDIT_PROMPT, what=what, html=html, text=text),
                         system=audit_system(paper), schema=P.AUDIT_SCHEMA, max_turns=6)
    return run.data


async def read_page(team: Team, plan: dict, persona: P.Persona, page: str, shots: list[str], played: str, cfg: Config,
                    r: int) -> dict:
    p = plan["paper"]
    description = fill(persona.description, field=p.get("field") or "this research field",
                       practitioner=p.get("practitioner") or "professional who might use this research")
    how = fill(P.READER_WITH_SCREENS, shots=", ".join(shots),
               played=fill(P.READER_PLAYED, played=played) if played else "") if shots else P.READER_TEXT_ONLY
    prompt = fill(P.READER_PROMPT, who=persona.who, short=persona.short, description=description, how=how, page=page)
    run = await team.run(f"reader-{persona.id}-r{r}", prompt, system=P.READER_SYSTEM, tools=["Read"] if shots else [],
                         schema=P.READER_SCHEMA, model=cfg.reader_model, max_turns=40)
    return run.data


async def peer_review(team: Team, paper: Paper, plan: dict, expert: dict, page: str, shots: list[str], cfg: Config,
                      r: int) -> dict:
    prompt = fill(P.PEER_PROMPT, name=expert["name"], expertise=expert["expertise"], own=own_list(plan, expert), round=str(r),
                  shots=f", and these screenshots of the page: {', '.join(shots)}" if shots else "", page=page)
    run = await team.run(f"peer-{expert['id']}-r{r}", prompt, system=expert_system(paper, team.ws), tools=LOOK_TOOLS,
                         schema=P.PEER_SCHEMA, effort=cfg.expert_effort, max_turns=30)
    return run.data


async def review(team: Team, browser: Browser, paper: Paper, plan: dict, cfg: Config, r: int, draft: Path) -> dict:
    rdir = team.ws / "rounds" / str(r)
    page_html = draft.read_text()
    text = page_text(page_html)
    qa = await cached(rdir / "qa.json", partial(technical_qa, team.ws, plan, browser, draft, rdir / "screens"))
    plays = qa.get("plays", [])
    shots = [str(team.ws / s) for s in qa["screens"]] + [str(team.ws / p) for p, _ in plays]
    played = "; ".join(f"{Path(p).name} shows {what}" for p, what in plays)
    jobs = []  # (kind, key, job)
    for persona in cfg.personas:
        jobs.append(("readers", f"{persona.id}: {persona.who}", cached(
            rdir / f"reader-{persona.id}.json", partial(read_page, team, plan, persona, text, shots, played, cfg, r))))
    for part in audit_parts(team.ws, plan, page_html):
        jobs.append(("audits", part[0], cached(rdir / f"audit-{part[0]}.json", partial(audit, team, paper, part, f"r{r}"))))
    if cfg.peer_review and len(plan["experts"]) > 1:
        for e in plan["experts"]:
            jobs.append(("peers", e["name"], cached(
                rdir / f"peer-{e['id']}.json", partial(peer_review, team, paper, plan, e, text, shots, cfg, r))))
    results = await run_all([guarded(team, f"{kind}:{key}", job) for kind, key, job in jobs])
    feedback = {"page": text, "qa": qa, "readers": {}, "audits": {}, "peers": {}}
    for (kind, key, _), data in zip(jobs, results):
        if data is not None:
            feedback[kind][key] = data
    return feedback


def majors(audits: dict) -> int:
    return sum(i["severity"] == "major" for a in audits.values() for i in a.get("issues", []))


def minors(audits: dict) -> int:
    return sum(i["severity"] == "minor" for a in audits.values() for i in a.get("issues", []))


def scores(feedback: dict) -> dict:
    return {"readers": {k.split(":")[0]: v.get("score") for k, v in feedback["readers"].items()},
            "fact_check": {"major": majors(feedback["audits"]), "minor": minors(feedback["audits"])},
            "browser_problems": sum(len(v) for v in feedback["qa"]["problems"].values()),
            "words": sum((feedback["qa"].get("words") or {}).values())}


# ---------------------------------------------------------------- 4. edit

def fallback_edit(feedback: dict) -> dict:
    """If the editor fails, still act on the fact-check and the browser checks."""
    tickets = [{"section_id": sid, "priority": "must", "instruction": f"{i['problem']} Fix: {i['fix']}", "why": "fact-check"}
               for sid, a in feedback["audits"].items() if sid != "hero" for i in a.get("issues", [])]
    tickets += [{"section_id": sid, "priority": "must", "instruction": p, "why": "browser check"}
                for sid, found in feedback["qa"]["problems"].items() for p in found]
    return {"assessment": "(the editor was unavailable; fixing fact-check and browser problems only)", "done": False,
            "tickets": tickets, "hero": {k: "" for k in HERO_FIELDS}, "key_numbers": [], "glossary_add": [], "notes_to_team": ""}


def length_report(qa: dict) -> str:
    words = qa.get("words") or {}
    if not words:
        return "(not measured)"
    lines = [f"- {sid}: {n}" + (" (over budget)" if n > P.SECTION_WORDS else "") for sid, n in words.items()]
    total = sum(words.values())
    return "\n".join(lines + [f"- whole page: {total}" + (" (over budget)" if total > P.PAGE_WORDS else "")])


async def decide(team: Team, paper: Paper, plan: dict, feedback: dict, cfg: Config, r: int) -> dict:
    async def make():
        prompt = fill(P.EDIT_PROMPT, round=str(r), rounds=str(cfg.rounds), plan=jdump(plan), page=feedback["page"],
                      readers=jdump(feedback["readers"]), audits=jdump(feedback["audits"]),
                      peers=jdump(feedback["peers"]) if feedback["peers"] else "(no peer review this round)",
                      qa=jdump(feedback["qa"]["problems"]) if feedback["qa"]["problems"] else "(no problems found)",
                      words=str(P.SECTION_WORDS), page_words=str(P.PAGE_WORDS), length=length_report(feedback["qa"]))
        run = await team.run(f"lead-edit-r{r}", prompt, system=lead_system(paper), tools=LOOK_TOOLS,
                             schema=P.EDIT_SCHEMA, max_turns=30)
        return run.data
    edit = await guarded(team, f"lead-edit-r{r}", cached(team.ws / "rounds" / str(r) / "edit.json", make))
    return edit or fallback_edit(feedback)


def apply_edit(plan: dict, edit: dict) -> None:
    for k, v in (edit.get("hero") or {}).items():
        if k in HERO_FIELDS and isinstance(v, str) and v.strip():
            plan["hero"][k] = v.strip()
    if edit.get("key_numbers"):
        plan["hero"]["key_numbers"] = edit["key_numbers"]
    terms = {g["term"].lower() for g in plan["glossary"]}
    for g in edit.get("glossary_add") or []:
        if g["term"].lower() not in terms:
            plan["glossary"].append(g)
            terms.add(g["term"].lower())


def feedback_for(ids: set, feedback: dict) -> dict:
    """The raw feedback that concerns these sections."""
    out = {"readers": [], "fact_check": [], "peer_review": [], "browser": []}
    for reader, data in feedback["readers"].items():
        who = reader.split(": ", 1)[-1]
        out["readers"] += [{"reader": who, **c} for c in data.get("confusing", []) if c["section_id"] in ids]
        out["readers"] += [{"reader": who, **v} for v in data.get("visuals", []) if v["section_id"] in ids]
        out["readers"] += [{"reader": who, **s} for s in data.get("section_scores", []) if s["section_id"] in ids]
        out["readers"] += [{"reader": who, "felt_repetitive": c["what"], "section_id": c["section_id"]}
                           for c in data.get("repetitive", []) if c["section_id"] in ids]
    for sid in sorted(ids):
        out["fact_check"] += [{"section_id": sid, **i} for i in feedback["audits"].get(sid, {}).get("issues", [])]
        out["browser"] += [f"[{sid}] {p}" for p in feedback["qa"]["problems"].get(sid, [])]
    for author, data in feedback["peers"].items():
        out["peer_review"] += [{"from": author, **c} for c in data.get("comments", []) if c["section_id"] in ids]
    return out


def team_notes(edit: dict, feedback: dict) -> str:
    notes = [edit.get("notes_to_team") or ""]
    notes += [f"From {author}: {n}" for author, data in feedback["peers"].items() for n in data.get("notes_to_team", [])]
    terms = sorted({t for data in feedback["readers"].values() for t in data.get("unexplained_terms", [])})
    if terms:
        notes.append("Words readers didn't understand (define them where they first appear, in your sections too): " + ", ".join(terms))
    return "\n".join(f"- {n}" for n in notes if n.strip()) or "(none)"


async def revise(team: Team, browser: Browser, paper: Paper, plan: dict, edit: dict, feedback: dict, cfg: Config,
                 state: State, r: int) -> None:
    notes = team_notes(edit, feedback)

    async def one(expert: dict):
        key = f"revise:{r}:{expert['id']}"
        if key in state:
            return
        ids = {s["id"] for s in owned(plan, expert)}
        tickets = [t for t in edit["tickets"] if t["section_id"] in ids]
        mine = feedback_for(ids, feedback)
        if not tickets and not mine["fact_check"] and not mine["browser"]:
            state.mark(key, "nothing to change")
            return
        prompt = fill(P.EXPERT_REVISE_PROMPT, name=expert["name"], expertise=expert["expertise"], own=own_list(plan, expert),
                      round=str(r), tickets=jdump(tickets) if tickets else "(no tickets: just address the feedback)",
                      feedback=jdump(mine), notes=notes, page=feedback["page"])
        run = await team.run(f"expert-{expert['id']}-revise-r{r}", prompt, system=expert_system(paper, team.ws), tools=EDIT_TOOLS,
                             writable=section_files(team.ws, plan, expert), mcp=tools_for(team, plan, paper, browser, cfg),
                             schema=P.SUMMARY_SCHEMA, need_data=False, effort=cfg.expert_effort,
                                 max_turns=cfg.expert_turns)
        state.mark(key, run.data)

    await run_all([guarded(team, f"expert-{e['id']}-revise-r{r}", one(e)) for e in plan["experts"]])


# ---------------------------------------------------------------- 5. verify

async def fix_hero(team: Team, paper: Paper, plan: dict, issues: list, state: State) -> None:
    current = {"hero": {k: plan["hero"][k] for k in HERO_FIELDS}, "key_numbers": plan["hero"]["key_numbers"],
               "glossary": plan["glossary"]}
    run = await team.run("lead-hero-fix", fill(P.HERO_FIX_PROMPT, issues=jdump(issues), hero=jdump(current)),
                         system=lead_system(paper), schema=P.HERO_FIX_SCHEMA, max_turns=6)
    plan["hero"].update({k: v for k, v in run.data["hero"].items() if k in HERO_FIELDS and v.strip()})
    plan["hero"]["key_numbers"] = run.data["key_numbers"]
    plan["glossary"] = run.data["glossary"] or plan["glossary"]
    save_plan(team.ws, plan)
    state.mark("fix:hero", run.data)


async def verify(team: Team, browser: Browser, paper: Paper, plan: dict, cfg: Config, state: State, rounds: int) -> dict:
    vdir = team.ws / "final"
    draft = write_draft(team.ws, plan, cfg, "final-check", rounds)
    qa = await cached(vdir / "qa.json", partial(technical_qa, team.ws, plan, browser, draft, None))
    parts = audit_parts(team.ws, plan, draft.read_text())
    results = await run_all([guarded(team, f"audit-{p[0]}-final", cached(vdir / f"audit-{p[0]}.json",
                                                                         partial(audit, team, paper, p, "final")))
                             for p in parts])
    audits = {p[0]: a for p, a in zip(parts, results) if a is not None}

    async def fix(expert: dict):
        key = f"fix:{expert['id']}"
        if key in state:
            return
        ids = [s["id"] for s in owned(plan, expert)]
        issues = [{"section_id": sid, **i} for sid in ids for i in audits.get(sid, {}).get("issues", [])]
        issues += [{"section_id": sid, "browser_problem": p} for sid in ids for p in qa["problems"].get(sid, [])]
        if not issues:
            state.mark(key, "clean")
            return
        run = await team.run(f"expert-{expert['id']}-fix",
                             fill(P.EXPERT_FIX_PROMPT, name=expert["name"], expertise=expert["expertise"],
                                  own=own_list(plan, expert), issues=jdump(issues)),
                             system=expert_system(paper, team.ws), tools=EDIT_TOOLS, writable=section_files(team.ws, plan, expert),
                             mcp=tools_for(team, plan, paper, browser, cfg), schema=P.SUMMARY_SCHEMA, need_data=False,
                             effort=cfg.expert_effort, max_turns=40)
        state.mark(key, run.data)

    jobs = [guarded(team, f"expert-{e['id']}-fix", fix(e)) for e in plan["experts"]]
    hero_issues = audits.get("hero", {}).get("issues", [])
    if hero_issues and "fix:hero" not in state:
        jobs.append(guarded(team, "lead-hero-fix", fix_hero(team, paper, plan, hero_issues, state)))
    await run_all(jobs)
    return {"audits": audits, "qa": qa}


# ---------------------------------------------------------------- one paper, end to end

async def make_page(src: Path, ws: Path, out_html: Path, out_json: Path, cfg: Config) -> dict:
    started = time.time()
    paper = load_paper(src, ws)
    (ws / "sections").mkdir(parents=True, exist_ok=True)
    team = Team(ws, model=cfg.model, effort=cfg.effort, parallel=cfg.parallel, auth=cfg.auth)
    state = State(ws / "state.json")
    browser = Browser()
    if not await browser.start():
        team.say("browser", f"not available ({browser.note}); pages are only checked statically")
    history = []
    try:
        team.say("paper", f"{src.name}: {paper.kind}, {paper.pages or '?'} pages, {len(paper.text):,} characters, "
                          f"{len(paper.figures)} figures")
        plan = await plan_page(team, paper, cfg)
        await build(team, browser, paper, plan, cfg, state)
        rounds = 0
        for r in range(1, cfg.rounds + 1):
            rounds = r
            feedback = await review(team, browser, paper, plan, cfg, r, write_draft(ws, plan, cfg, f"v{r}", cfg.rounds))
            edit = await decide(team, paper, plan, feedback, cfg, r)
            apply_edit(plan, edit)
            save_plan(ws, plan)
            history.append({"round": r, **scores(feedback), "editor_done": bool(edit.get("done"))})
            team.say("lead", f"round {r}: readers {history[-1]['readers']}, fact-check {history[-1]['fact_check']}, "
                             f"{len(edit['tickets'])} tickets")
            if edit.get("done") and not majors(feedback["audits"]) and not feedback["qa"]["problems"]:
                team.say("lead", f"the editor is satisfied after round {r}")
                break
            await revise(team, browser, paper, plan, edit, feedback, cfg, state, r)
        final = await verify(team, browser, paper, plan, cfg, state, rounds)
        history.append({"round": "final", "fact_check": {"major": majors(final["audits"]), "minor": minors(final["audits"])},
                        "browser_problems": sum(len(v) for v in final["qa"]["problems"].values()),
                        "note": "counted before the final fixes"})
        page = inline_images(assemble(ws, plan, readers=reader_names(cfg), rounds=rounds), ws)
        out_html.parent.mkdir(parents=True, exist_ok=True)
        out_html.write_text(page)
        (ws / "final.html").write_text(page)
    finally:
        await browser.close()

    ledger = team.ledger
    meta = {
        "framework": "multiagent",
        "model": cfg.model,
        "reader_model": cfg.reader_model or cfg.model,
        "effort": cfg.effort,
        "expert_effort": cfg.expert_effort,
        "auth": cfg.auth,
        "paper": src.name,
        "rounds": rounds,
        "readers": cfg.readers,
        "experts": [{"id": e["id"], "name": e["name"], "sections": [s["id"] for s in owned(plan, e)]} for e in plan["experts"]],
        "history": history,
        "agent_sessions": len(ledger),
        "seconds": round(time.time() - started),
        "cost_usd_api_equivalent": round(sum(r["cost_usd"] for r in ledger), 2),
        "tokens": {k: sum(r[k] for r in ledger) for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")},
        "workspace": str(ws),
    }
    out_json.write_text(jdump(meta))
    return meta


async def run_one(src: Path, stem: str, out: Path, work: Path, cfg: Config, fresh: bool) -> dict | None:
    ws = (work / stem).resolve()
    out_html, out_json = out / f"{stem}.html", out / f"{stem}.json"
    if fresh and ws.exists():
        shutil.rmtree(ws)
    elif out_html.exists() and (ws / "final.html").exists():
        print(f"{stem}: already done ({out_html}); use --fresh to redo it")
        return None
    print(f"{stem} ... (workspace: {ws})")
    meta = await make_page(src, ws, out_html, out_json, cfg)
    print(f"  wrote {out_html} ({out_html.stat().st_size // 1000} KB) in {meta['seconds'] // 60} min, "
          f"{meta['agent_sessions']} agent sessions (API-equivalent cost ${meta['cost_usd_api_equivalent']})")
    for h in meta["history"]:
        readers = f"readers {h['readers']}, " if "readers" in h else ""
        print(f"  round {h['round']}: {readers}fact-check {h['fact_check']}, browser problems {h['browser_problems']}"
              + (f", {h['words']} words of main text" if h.get("words") else ""))
    return meta


# ---------------------------------------------------------------- command line

def add_options(parser: argparse.ArgumentParser) -> None:
    """Options shared with generate_missing.py."""
    ids = ", ".join(p.id for p in P.PERSONAS)
    parser.add_argument("--model", help="model for the editor, experts and fact-checker (default anthropic/claude-opus-5.5 on "
                        "OpenRouter, claude-opus-5-5 otherwise)")
    parser.add_argument("--reader-model", help="model for the simulated readers (default Sonnet 5.5: anthropic/claude-sonnet-5.5 "
                        "on OpenRouter, claude-sonnet-5-5 otherwise)")
    efforts = ["low", "medium", "high", "xhigh", "max"]
    parser.add_argument("--effort", default="high", choices=efforts, help="editor, fact-checker and readers (default high)")
    parser.add_argument("--expert-effort", default="medium", choices=efforts,
                        help="the experts: writing, revising, fixing and peer review (default medium)")
    parser.add_argument("--rounds", type=int, default=1, help="review-and-revise rounds (default 1)")
    parser.add_argument("--readers", default="default", help=f"comma-separated simulated readers from: {ids}; or 'all' "
                        f"(default: {','.join(DEFAULT_READERS)})")
    parser.add_argument("--max-experts", type=int, default=3, help="largest team the editor may pick (default 3)")
    parser.add_argument("--no-peer-review", action="store_true", help="skip the experts' reviews of each other's sections")
    parser.add_argument("--parallel", type=int, default=4, help="agents running at the same time (default 4)")
    parser.add_argument("--quick", action="store_true", help="a cheaper run: 1 round, 3 readers, at most 3 experts, no peer review")
    parser.add_argument("--auth", choices=["openrouter", "subscription", "api-key"], default="openrouter",
                        help="openrouter (default): OPENROUTER_API_KEY / LLM_API_KEY from .env. subscription: your Claude "
                             "login in Claude Code. api-key: ANTHROPIC_API_KEY from .env")
    parser.add_argument("--out", type=Path, default=HERE / "outputs")
    parser.add_argument("--work", type=Path, default=HERE / "work", help="where run workspaces go (drafts, feedback, logs)")
    parser.add_argument("--fresh", action="store_true", help="start over instead of resuming an existing workspace")


def config_from(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Config:
    cfg = Config(model=args.model or DEFAULT_MODEL[args.auth], reader_model=args.reader_model or READER_MODEL[args.auth], effort=args.effort,
                 expert_effort=args.expert_effort, rounds=args.rounds,
                 max_experts=args.max_experts, peer_review=not args.no_peer_review, parallel=args.parallel, auth=args.auth)
    if args.readers == "all":
        cfg.readers = [p.id for p in P.PERSONAS]
    elif args.readers != "default":
        cfg.readers = [r.strip() for r in args.readers.split(",") if r.strip()]
        unknown = set(cfg.readers) - {p.id for p in P.PERSONAS}
        if unknown:
            parser.error(f"unknown reader(s): {', '.join(sorted(unknown))}")
    if args.quick:
        cfg.rounds = min(cfg.rounds, 1)
        cfg.max_experts = min(cfg.max_experts, 3)
        cfg.max_sections = 4
        cfg.peer_review = False
        if args.readers == "default":
            cfg.readers = ["kid", "grad", "professor"]
    return cfg


def run_stem(src: Path, model: str, i: int) -> str:
    # one name per model whatever the auth, so a run started on OpenRouter can resume on a subscription:
    # claude-opus-5-5 is named like OpenRouter's anthropic/claude-opus-5.5
    if "/" not in model:
        model = "anthropic/" + re.sub(r"-(\d+)-(\d+)$", r"-\1.\2", model)
    return f"{src.stem}__{model.replace('/', '_')}__run{i}"


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", type=Path, help="the paper: a PDF, or JATS XML")
    parser.add_argument("--runs", type=int, default=1, help="number of runs (the evaluation uses best of 3)")
    add_options(parser)
    args = parser.parse_args()
    cfg = config_from(args, parser)
    use_auth(cfg.auth)
    try:
        for i in range(1, args.runs + 1):
            asyncio.run(run_one(args.pdf, run_stem(args.pdf, cfg.model, i), args.out, args.work, cfg, args.fresh))
    except UsageLimit as e:
        sys.exit(f"stopped: {e}")


if __name__ == "__main__":
    main()
