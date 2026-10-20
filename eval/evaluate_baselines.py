"""Evaluate baseline HTML files against their source papers with an LLM judge.

Run from any directory:
    python eval/evaluate_baselines.py
    python eval/evaluate_baselines.py --pair paper_id=path/to/baseline.html

By default, HTML names must start with the paper filename followed by ``__``.
Each matched paper/HTML pair gets its own independent API request.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pypdf import PdfReader


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PAPERS = PROJECT_DIR / "eval" / "papers"
DEFAULT_HTML = PROJECT_DIR / "baseline" / "outputs" / "without_agent"
DEFAULT_PROMPT = PROJECT_DIR / "eval" / "prompt.md"
DEFAULT_OUTPUT = PROJECT_DIR / "eval" / "results"
SUPPORTED_PAPER_SUFFIXES = {".pdf", ".xml"}


def resolve_from_project(path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    from_current_directory = path.resolve()
    if from_current_directory.exists():
        return from_current_directory
    return (PROJECT_DIR / path).resolve()


def discover_pairs(papers_dir: Path, html_dir: Path) -> list[tuple[Path, Path]]:
    papers = sorted(
        (path for path in papers_dir.iterdir()
         if path.is_file() and path.suffix.lower() in SUPPORTED_PAPER_SUFFIXES),
        key=lambda path: len(path.stem),
        reverse=True,
    )
    html_files = sorted(path for path in html_dir.glob("*.html") if path.is_file())
    pairs = []
    unmatched = []
    for html_path in html_files:
        paper = next(
            (candidate for candidate in papers
             if html_path.stem == candidate.stem
             or html_path.stem.startswith(candidate.stem + "__")),
            None,
        )
        if paper is None:
            unmatched.append(html_path.name)
        else:
            pairs.append((paper, html_path))
    if unmatched:
        print("Skipping HTML files without a matching paper stem:", file=sys.stderr)
        for name in unmatched:
            print(f"  {name}", file=sys.stderr)
    return pairs


def explicit_pairs(specs: list[str], papers_dir: Path) -> list[tuple[Path, Path]]:
    papers = {
        path.stem: path
        for path in papers_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_PAPER_SUFFIXES
    }
    pairs = []
    for spec in specs:
        paper_id, separator, html_value = spec.partition("=")
        if not separator or not paper_id or not html_value:
            raise ValueError(f"Invalid --pair {spec!r}; expected PAPER_ID=HTML_PATH")
        if paper_id not in papers:
            raise ValueError(f"No PDF/XML named {paper_id!r} in {papers_dir}")
        html_path = resolve_from_project(Path(html_value))
        if not html_path.is_file() or html_path.suffix.lower() != ".html":
            raise ValueError(f"HTML file does not exist: {html_path}")
        pairs.append((papers[paper_id], html_path))
    return pairs


def parse_agent_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", candidate, re.S | re.I)
    if fenced:
        candidate = fenced.group(1)
    try:
        result = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise ValueError(f"Agent did not return valid JSON: {error}") from error
    if not isinstance(result, dict):
        raise ValueError("Agent response must be a JSON object")
    hallucinations = result.get("hallucinations", {})
    items = hallucinations.get("items", []) if isinstance(hallucinations, dict) else []
    count = hallucinations.get("count") if isinstance(hallucinations, dict) else None
    if count != len(items):
        raise ValueError("Invalid evaluation JSON: hallucinations.count must equal items.length")
    return result


def read_paper(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        text = "\n\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    else:
        text = path.read_text(encoding="utf-8")
    return text.encode("utf-8", errors="replace").decode("utf-8")


def evaluate_one(
    client: OpenAI,
    paper_path: Path,
    html_path: Path,
    prompt_template: str,
    output_path: Path,
    model: str,
) -> None:
    prompt = (prompt_template
              .replace("{{PAPER_PATH}}", str(paper_path))
              .replace("{{HTML_PATH}}", str(html_path))
              .replace("{{OUTPUT_PATH}}", str(output_path)))
    paper_text = read_paper(paper_path)
    html_text = html_path.read_text(encoding="utf-8", errors="replace")
    response = client.chat.completions.create(
        model=model,
        max_tokens=16000,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a rigorous scientific-paper evaluator. Follow the user rubric, "
                    "use only evidence present in the supplied source, and return one valid "
                    "JSON object matching the requested schema. Do not use markdown fences."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{prompt}\n\n"
                    f"<paper path={json.dumps(str(paper_path))}>\n{paper_text}\n</paper>\n\n"
                    f"<html path={json.dumps(str(html_path))}>\n{html_text}\n</html>\n\n"
                    "Evaluate the complete supplied paper and HTML. Return only the JSON object; "
                    "the runner will write it to the output path."
                ),
            },
        ],
    )
    response_text = response.choices[0].message.content or ""
    if not response_text.strip():
        raise ValueError("Agent completed without returning an evaluation")

    evaluation = parse_agent_json(response_text)
    evaluation["paper_id"] = paper_path.stem
    evaluation["html_path"] = str(html_path)
    evaluation["evaluator_model"] = model
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(evaluation, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--papers", type=Path, default=DEFAULT_PAPERS,
                        help="directory containing source PDFs/XML (default: eval/papers)")
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML,
                        help="directory containing baseline HTML (default: baseline/outputs/without_agent)")
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT,
                        help="evaluation prompt markdown file")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT,
                        help="directory for evaluation JSON files")
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL"),
                        help="model name (defaults to LLM_MODEL from .env)")
    parser.add_argument("--pair", action="append", default=[], metavar="PAPER_ID=HTML_PATH",
                        help="explicit pairing; can be repeated, disables automatic pairing")
    args = parser.parse_args()
    load_dotenv(PROJECT_DIR / ".env")
    model = args.model or os.environ.get("LLM_MODEL")
    api_key = os.environ.get("LLM_API_KEY")
    if not model:
        parser.error("no model: pass --model or set LLM_MODEL in paper-viewer/.env")
    if not api_key:
        parser.error("no API key: set LLM_API_KEY in paper-viewer/.env or the environment")

    papers_dir = resolve_from_project(args.papers)
    html_dir = resolve_from_project(args.html)
    prompt_path = resolve_from_project(args.prompt)
    output_dir = resolve_from_project(args.out)
    for path in (papers_dir, html_dir, prompt_path):
        if not path.exists():
            parser.error(f"path does not exist: {path}")

    try:
        pairs = explicit_pairs(args.pair, papers_dir) if args.pair else discover_pairs(papers_dir, html_dir)
    except ValueError as error:
        parser.error(str(error))
    if not pairs:
        parser.error("no paper/HTML pairs found; use --pair PAPER_ID=HTML_PATH for custom filenames")

    prompt_template = prompt_path.read_text()
    client = OpenAI(api_key=api_key, base_url=os.environ.get("LLM_BASE_URL"))
    failed = 0
    for paper_path, html_path in pairs:
        output_path = output_dir / f"{paper_path.stem}__{html_path.stem}.json"
        print(f"Evaluating {paper_path.name} against {html_path.name} ...", flush=True)
        try:
            evaluate_one(client, paper_path, html_path, prompt_template, output_path, model)
            print(f"  wrote {output_path}", flush=True)
        except Exception as error:
            failed += 1
            print(f"  failed: {error}", file=sys.stderr, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())