"""Baseline: a single LLM call, no tools, no subagents.

Extracts the paper's text and asks one model for an interactive HTML explanation.
Run it with two models of different capability to get the two baselines.
Credentials come from .env: LLM_API_KEY, LLM_BASE_URL (OpenRouter), LLM_MODEL.

    python baseline.py paper.pdf --model <model-a> --runs 3
    python baseline.py paper.pdf --model <model-b> --runs 3
"""
import argparse
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pypdf import PdfReader

SYSTEM = """You turn research papers into interactive webpages.

Given a research paper, produce ONE self-contained HTML file that explains it to a
curious general audience. Cover the problem, the key idea, the method, the results,
and the limitations. Explain equations in plain language. Make the page interactive
where it helps understanding (sliders, toggles, clickable diagrams, charts).

Rules:
- Output only the HTML document, starting with <!DOCTYPE html>. No commentary.
- Inline all CSS and JavaScript. Plotly may be loaded from its CDN if you need charts.
- Only state facts that are in the paper. Do not invent results, numbers, or citations."""


def pdf_text(path: Path) -> str:
    text = "\n\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    # Some PDF fonts extract as lone surrogates, which the API can't encode as UTF-8; drop them.
    return text.encode("utf-8", "ignore").decode("utf-8")


def extract_html(text: str) -> str:
    fenced = re.search(r"```(?:html)?\s*(<!DOCTYPE.*?)```", text, re.S | re.I)
    return (fenced.group(1) if fenced else text).strip()


def run_once(client: OpenAI, model: str, paper: str) -> tuple[str, dict]:
    response = client.chat.completions.create(
        model=model,
        max_tokens=32000,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Explain this paper as an interactive webpage.\n\n<paper>\n{paper}\n</paper>"},
        ],
    )
    choice = response.choices[0]
    if choice.finish_reason == "length":
        print(f"  warning: {model} hit the token limit, HTML may be truncated")

    usage = {
        "input_tokens": response.usage.prompt_tokens if response.usage else None,
        "output_tokens": response.usage.completion_tokens if response.usage else None,
        "finish_reason": choice.finish_reason,
    }
    return extract_html(choice.message.content or ""), usage


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL"), help="defaults to LLM_MODEL from .env")
    parser.add_argument("--runs", type=int, default=1, help="number of runs (the evaluation uses best of 3)")
    parser.add_argument("--out", type=Path, default=Path("outputs/baseline"))
    args = parser.parse_args()

    if not args.model:
        parser.error("no model: pass --model or set LLM_MODEL in .env")

    client = OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.environ.get("LLM_BASE_URL"))
    paper = pdf_text(args.pdf)

    args.out.mkdir(parents=True, exist_ok=True)
    safe_model = args.model.replace("/", "_")
    for i in range(1, args.runs + 1):
        stem = f"{args.pdf.stem}__{safe_model}__run{i}"
        print(f"{stem} ...")
        html, usage = run_once(client, args.model, paper)
        (args.out / f"{stem}.html").write_text(html)
        (args.out / f"{stem}.json").write_text(json.dumps({"model": args.model, **usage}, indent=2))
        print(f"  wrote {args.out / f'{stem}.html'} ({usage['output_tokens']} output tokens)")


if __name__ == "__main__":
    main()
