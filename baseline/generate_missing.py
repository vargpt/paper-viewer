"""Create the missing baseline HTML explainers for the eval papers.

For each paper in eval/papers/ (PDF, or JATS XML), looks in the output folder
(and its sub-folders) for an HTML named <paper_id>__<model>__run*.html. If there
is none, it generates one with the same single LLM call as baseline.py.
Credentials come from .env: LLM_API_KEY, LLM_BASE_URL (OpenRouter), LLM_MODEL.

    python baseline/generate_missing.py --dry-run          # only list what is missing
    python baseline/generate_missing.py --model <model>
"""
import argparse
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from baseline import pdf_text, run_once

ROOT = Path(__file__).resolve().parent.parent
PAPER_TYPES = {".pdf", ".xml"}


def xml_text(path: Path) -> str:
    # JATS XML: drop the reference list, keep every other piece of text.
    root = ET.parse(path).getroot()
    for parent in root.iter():
        for child in list(parent):
            if child.tag.split("}")[-1] == "ref-list":
                parent.remove(child)
    return "\n".join(t.strip() for t in root.itertext() if t.strip())


def paper_text(path: Path) -> str:
    return pdf_text(path) if path.suffix.lower() == ".pdf" else xml_text(path)


def existing_html(out: Path, paper_id: str, safe_model: str) -> list[Path]:
    return sorted(out.rglob(f"{paper_id}__{safe_model}__run*.html"))


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--papers", type=Path, default=ROOT / "eval/papers")
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL"), help="defaults to LLM_MODEL from .env")
    parser.add_argument("--out", type=Path, default=ROOT / "baseline/outputs/without_agent")
    parser.add_argument("--dry-run", action="store_true", help="list missing papers without calling the model")
    args = parser.parse_args()

    if not args.model:
        parser.error("no model: pass --model or set LLM_MODEL in .env")

    safe_model = args.model.replace("/", "_")
    papers = sorted(p for p in args.papers.iterdir() if p.suffix.lower() in PAPER_TYPES)
    missing = []
    for paper in papers:
        found = existing_html(args.out, paper.stem, safe_model)
        print(f"{'ok     ' if found else 'missing'} {paper.name}" + (f"  ({found[0].relative_to(args.out)})" if found else ""))
        if not found:
            missing.append(paper)

    print(f"\n{len(papers) - len(missing)} of {len(papers)} papers have an HTML for {args.model}.")
    if args.dry_run or not missing:
        return

    client = OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.environ.get("LLM_BASE_URL"))
    args.out.mkdir(parents=True, exist_ok=True)
    for paper in missing:
        stem = f"{paper.stem}__{safe_model}__run1"
        print(f"{stem} ...")
        try:
            html, usage = run_once(client, args.model, paper_text(paper))
        except Exception as e:  # keep going so one failure doesn't stop the batch
            print(f"  failed: {e}")
            continue
        (args.out / f"{stem}.html").write_text(html)
        (args.out / f"{stem}.json").write_text(json.dumps({"model": args.model, "paper": paper.name, **usage}, indent=2))
        print(f"  wrote {args.out / f'{stem}.html'} ({usage['output_tokens']} output tokens)")


if __name__ == "__main__":
    main()
