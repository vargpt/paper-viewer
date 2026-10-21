"""Run the whole pipeline with stub agents: no model calls, no usage. Checks the orchestration.

    python multiagent/test_pipeline.py            # or: pytest multiagent/test_pipeline.py

Stub agents answer every schema with canned data and write simple sections, so plan -> build ->
review/edit rounds -> verify -> resume all run (with real browser checks when Playwright is there).
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import multiagent
import prompts as P
from agents import Run

PAPER = multiagent.ROOT / "eval/papers/Konate_SphereEdit_WACV2026.pdf"


class StubTeam(multiagent.Team):
    calls: list[str] = []

    async def run(self, name, prompt, *, system, tools=(), writable=(), mcp=None, schema=None, need_data=True,
                  model=None, max_turns=40, retries=2):
        StubTeam.calls.append(name)
        plan_path = self.ws / "plan.json"
        plan = json.loads(plan_path.read_text()) if plan_path.exists() else None
        ids = [s["id"] for s in plan["sections"]] if plan else []
        data = None
        if schema is P.PLAN_SCHEMA:
            data = _plan()
        elif schema is P.SUMMARY_SCHEMA:
            for f in writable:
                sid = Path(f).stem
                Path(f).write_text(f'<section class="chapter" id="{sid}"><h2>{sid} ({name})</h2>'
                                   f'<p class="tldr">In one sentence.</p><div class="chart" id="{sid}-c" role="img" aria-label="x"></div>'
                                   f'<script>(() => {{ PV.plot("{sid}-c", [{{x: [1, 2], y: [3, 4]}}]); }})();</script></section>')
            data = {"summary": "done", "visuals": [], "concerns": []}
        elif schema is P.READER_SCHEMA:
            data = {"score": 6, "teach_back": "t", "stopped_at": "read to the end",
                    "confusing": [{"section_id": ids[0], "quote": "q", "why": "w"}], "unexplained_terms": ["latent"],
                    "visuals": [{"section_id": ids[-1], "verdict": "helped", "note": "n"}], "liked": [], "requests": ["r"],
                    "section_scores": [{"section_id": i, "score": 6} for i in ids]}
        elif schema is P.AUDIT_SCHEMA:
            data = {"issues": [{"quote": "q", "problem": "p", "paper_says": "s", "severity": "minor", "fix": "f"}], "summary": "s"}
        elif schema is P.PEER_SCHEMA:
            data = {"comments": [{"section_id": ids[0], "kind": "consistency", "comment": "c", "suggestion": "s"}],
                    "notes_to_team": ["use one name"]}
        elif schema is P.EDIT_SCHEMA:
            data = {"assessment": "a", "done": False, "tickets": [{"section_id": i, "priority": "must", "instruction": "i", "why": "w"} for i in ids],
                    "hero": {k: "" for k in multiagent.HERO_FIELDS} | {"kid": "A clearer kid summary."}, "key_numbers": [],
                    "glossary_add": [{"term": "Latent", "definition": "d"}], "notes_to_team": "n"}
        elif schema is P.HERO_FIX_SCHEMA:
            data = {"hero": plan["hero"] | {"subtitle": "fixed"}, "key_numbers": plan["hero"]["key_numbers"], "glossary": plan["glossary"]}
        self.ledger.append({"agent": name, "model": self.model, "seconds": 0, "turns": 1, "cost_usd": 0.0, "input_tokens": 0,
                            "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0})
        return Run(name, data, "", 0.0, 1, 0.0)


def _plan() -> dict:
    hero = {"headline": "h", "subtitle": "s", "kid": "k", "general": "g", "expert": "e", "why_it_matters": "w",
            "key_numbers": [{"value": "1", "label": "l", "source": "Table 1"}]}
    section = {"goal": "g", "key_points": ["k"], "paper_refs": "p", "visuals": ["v"], "figures": ["fig1", "fig99"]}
    return {"paper": {"title": "t", "authors": "a", "venue": "v", "year": "y", "field": "f", "practitioner": "p", "problem": "p",
                      "key_idea": "k", "method": "m", "results": [], "limitations": []},
            "hero": hero,
            "experts": [{"id": "Story Teller", "name": "Storyteller", "expertise": "e", "brief": "b"},
                        {"id": "geo", "name": "Geometer", "expertise": "e", "brief": "b"},
                        {"id": "data", "name": "Analyst", "expertise": "e", "brief": "b"}],
            "sections": [{"id": "Why it matters", "title": "Why", "owner": "Story Teller", **section},
                         {"id": "idea", "title": "Idea", "owner": "geo", **section},
                         {"id": "glossary", "title": "Reserved id", "owner": "Analyst", **section},
                         {"id": "limits", "title": "Limits", "owner": "nobody", **section}],
            "glossary": [{"term": "Diffusion", "definition": "d"}], "notes": "n"}


def test_pipeline() -> None:
    multiagent.Team = StubTeam
    cfg = multiagent.Config(rounds=2, readers=[p.id for p in P.PERSONAS])  # all six readers, peer review on
    with tempfile.TemporaryDirectory() as tmp:
        out, work = Path(tmp) / "out", Path(tmp) / "work"
        meta = asyncio.run(multiagent.run_one(PAPER, "stub", out, work, cfg, fresh=False))
        plan = json.loads((work / "stub" / "plan.json").read_text())
        assert [s["id"] for s in plan["sections"]] == ["why-it-matters", "idea", "glossary-section", "limits"]
        assert {s["owner"] for s in plan["sections"]} <= {e["id"] for e in plan["experts"]}
        assert all(s["figures"] == ["fig1"] for s in plan["sections"])
        assert plan["hero"]["kid"] == "A clearer kid summary." and plan["hero"]["subtitle"] == "fixed"
        assert any(g["term"] == "Latent" for g in plan["glossary"])
        names = StubTeam.calls
        assert sum(n.startswith("reader-") for n in names) == 12 and sum(n.startswith("peer-") for n in names) == 6
        assert sum("-revise-r" in n for n in names) == 6 and sum(n.endswith("-fix") for n in names) == 4
        assert [h["round"] for h in meta["history"]] == [1, 2, "final"]
        page = (out / "stub.html").read_text()
        assert "data:image/png;base64" in page or "fig1" not in page
        assert "A clearer kid summary." in page and "Glossary" in page
        calls = len(names)
        asyncio.run(multiagent.run_one(PAPER, "stub", out, work, cfg, fresh=False))  # finished runs are skipped
        (out / "stub.html").unlink()
        asyncio.run(multiagent.run_one(PAPER, "stub", out, work, cfg, fresh=False))  # resume: everything is cached
        assert len(StubTeam.calls) == calls, StubTeam.calls[calls:]
        print(f"ok: {calls} stub agent sessions; history {json.dumps(meta['history'])}")


if __name__ == "__main__":
    test_pipeline()
