"""Pairwise comparison of two explainer pages for the same paper, judged by an LLM.

The judge sees the paper and both pages side by side and picks the better one for each reader age.
Every pair is judged twice with the order swapped (position bias), and a page only wins an age group if
both orders agree; otherwise that age is a tie.

    python eval/pairwise.py \
        --pair "2404.14414=baseline.html=candidate.html" \
        --out multiagent/outputs/eval/pairwise

Inline base64 images are removed from the pages first (they are huge and the judge cannot use them as text).
Credentials come from .env: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL (override with --model).
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pypdf import PdfReader

PROJECT_DIR = Path(__file__).resolve().parents[1]
PAPERS = PROJECT_DIR / "eval" / "papers"
AGES = [5, 15, 25, 35, 45, 55, 65]

SYSTEM = ("You are a rigorous judge of science explainer webpages. Use only evidence in the supplied paper and "
          "pages, and return one valid JSON object. No markdown fences.")

PROMPT = """Two webpages, PAGE_1 and PAGE_2, each try to explain the same research paper. Compare them.

For each reader age in {ages}, imagine a typical reader of that age with no background in the field. Decide which
page explains the paper better to that reader: how much they would understand, how approachable and engaging it is,
and whether anything is confusing or too dense. Judge on content and structure; images were removed from the text you
see, so do not penalize missing pictures. Answer "1", "2" or "tie" (tie only if you truly cannot separate them), and
give one specific sentence of evidence from the pages.

Then list claims in each page (numbers, results, method details) that the paper does not support. Only real
unsupported claims; do not list simplifications or clearly labeled toy examples.

Write every string on one line. Inside a string never use quote characters or backslashes; rephrase instead.
Return one flat JSON object with exactly these keys (no nesting except the two lists):
  "age_5_winner", "age_5_why", "age_15_winner", "age_15_why", "age_25_winner", "age_25_why", "age_35_winner",
  "age_35_why", "age_45_winner", "age_45_why", "age_55_winner", "age_55_why", "age_65_winner", "age_65_why",
  "overall_winner", "overall_why", "unsupported_page_1", "unsupported_page_2"
Every *_winner is "1", "2" or "tie". Every *_why is one short sentence. The two unsupported_* values are lists of
short strings (empty list if none).
"""


def strip_images(html: str) -> str:
    return re.sub(r"data:[a-z/+.-]+;base64,[A-Za-z0-9+/=]+", "data:image-omitted", html)


def paper_text(path: Path) -> str:
    text = "\n\n".join(p.extract_text() or "" for p in PdfReader(path).pages)
    return text.encode("utf-8", errors="replace").decode("utf-8")


def parse_json(text: str) -> dict:
    """json.loads, tolerating code fences and \' escapes (not valid JSON, but models write them)."""
    return json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip()).strip().replace("\'", "'"))


def judge(client: OpenAI, model: str, paper: str, page1: str, page2: str, attempts: int = 2) -> dict:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": PROMPT.format(ages=", ".join(map(str, AGES)))
                 + f"\n<paper>\n{paper}\n</paper>\n\n<PAGE_1>\n{page1}\n</PAGE_1>\n\n<PAGE_2>\n{page2}\n</PAGE_2>\n"}]
    for attempt in range(attempts):
        response = client.chat.completions.create(model=model, max_tokens=8000, messages=messages,
                                                  response_format={"type": "json_object"})
        text = response.choices[0].message.content or ""
        try:
            return parse_json(text)
        except json.JSONDecodeError as error:
            if os.environ.get("PAIRWISE_DUMP"):
                Path(os.environ["PAIRWISE_DUMP"]).write_text(text)
            print(f"  judge returned invalid JSON ({error}); {'retrying' if attempt + 1 < attempts else 'giving up'}", flush=True)
            if attempt + 1 == attempts:
                raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pair", action="append", required=True, metavar="PAPER=BASELINE_HTML=CANDIDATE_HTML",
                        help="paper id (a file in eval/papers) and the two pages; repeatable")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default=None, help="judge model (default: LLM_MODEL from .env)")
    args = parser.parse_args()

    load_dotenv(PROJECT_DIR / ".env")
    model = args.model or os.environ.get("LLM_MODEL")
    if not model or not os.environ.get("LLM_API_KEY"):
        parser.error("need LLM_API_KEY and a model (--model or LLM_MODEL) in .env")
    client = OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.environ.get("LLM_BASE_URL"))
    args.out.mkdir(parents=True, exist_ok=True)

    for spec in args.pair:
        paper_id, base_path, cand_path = spec.split("=", 2)
        pdf = next(PAPERS.glob(f"{paper_id}.pdf"))
        paper = paper_text(pdf)
        base = strip_images(Path(base_path).read_text(errors="replace"))
        cand = strip_images(Path(cand_path).read_text(errors="replace"))
        print(f"{paper_id}: judging with {model}, both orders ...", flush=True)

        runs = {"baseline_first": judge(client, model, paper, base, cand),     # page 1 = baseline
                "candidate_first": judge(client, model, paper, cand, base)}    # page 1 = candidate

        def who(order: str, winner: str) -> str:
            if winner == "tie":
                return "tie"
            first_is_baseline = order == "baseline_first"
            return "baseline" if (winner == "1") == first_is_baseline else "candidate"

        by_age = {}
        for age in map(str, AGES):
            a = who("baseline_first", runs["baseline_first"][f"age_{age}_winner"])
            b = who("candidate_first", runs["candidate_first"][f"age_{age}_winner"])
            by_age[age] = {"verdict": a if a == b else "tie", "order_baseline_first": a, "order_candidate_first": b,
                           "why_baseline_first": runs["baseline_first"][f"age_{age}_why"],
                           "why_candidate_first": runs["candidate_first"][f"age_{age}_why"]}
        oa = who("baseline_first", runs["baseline_first"]["overall_winner"])
        ob = who("candidate_first", runs["candidate_first"]["overall_winner"])
        bf, cf = runs["baseline_first"], runs["candidate_first"]
        unsupported = {"baseline": bf["unsupported_page_1"] + cf["unsupported_page_2"],
                       "candidate": bf["unsupported_page_2"] + cf["unsupported_page_1"]}
        result = {"paper_id": paper_id, "judge": model, "baseline_html": base_path, "candidate_html": cand_path,
                  "overall": {"verdict": oa if oa == ob else "tie", "order_baseline_first": oa, "order_candidate_first": ob},
                  "overall_why": {"baseline_first": bf["overall_why"], "candidate_first": cf["overall_why"]},
                  "by_age": by_age, "unsupported_claims_reported": unsupported, "raw": runs}
        (args.out / f"{paper_id}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        wins = [v["verdict"] for v in by_age.values()]
        print(f"  overall: {result['overall']['verdict']} | ages: candidate {wins.count('candidate')}, "
              f"baseline {wins.count('baseline')}, tie {wins.count('tie')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
