"""Create the missing multi-agent explainers for the eval papers.

For each paper in eval/papers/ (PDF, or JATS XML), looks in the output folder for an HTML named
<paper_id>__<model>__run*.html. If there is none, it runs the multi-agent pipeline on that paper,
one paper at a time. Takes the same options as multiagent.py (default: OpenRouter key from .env).

    python multiagent/generate_missing.py --dry-run          # only list what is missing
    python multiagent/generate_missing.py
    python multiagent/generate_missing.py --quick            # cheaper runs, e.g. for a first look
"""
import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from agents import UsageLimit, use_auth
from multiagent import ROOT, add_options, config_from, run_one, run_stem

PAPER_TYPES = {".pdf", ".xml"}


def existing_html(out: Path, paper_id: str, safe_model: str) -> list[Path]:
    return sorted(out.rglob(f"{paper_id}__{safe_model}__run*.html"))


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--papers", type=Path, default=ROOT / "eval/papers")
    parser.add_argument("--dry-run", action="store_true", help="list missing papers without running any agents")
    add_options(parser)
    args = parser.parse_args()
    cfg = config_from(args, parser)

    safe_model = cfg.model.replace("/", "_")
    papers = sorted(p for p in args.papers.iterdir() if p.suffix.lower() in PAPER_TYPES)
    missing = []
    for paper in papers:
        found = existing_html(args.out, paper.stem, safe_model) if args.out.exists() else []
        print(f"{'ok     ' if found else 'missing'} {paper.name}" + (f"  ({found[0].relative_to(args.out)})" if found else ""))
        if not found:
            missing.append(paper)

    print(f"\n{len(papers) - len(missing)} of {len(papers)} papers have a multi-agent HTML for {cfg.model}.")
    if args.dry_run or not missing:
        return

    use_auth(cfg.auth)
    for paper in missing:
        try:
            asyncio.run(run_one(paper, run_stem(paper, cfg.model, 1), args.out, args.work, cfg, args.fresh))
        except UsageLimit as e:
            sys.exit(f"stopped: {e}")
        except Exception as e:  # keep going so one failure doesn't stop the batch
            print(f"  failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
