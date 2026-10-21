"""Paper → fun short: animated scenes built from the paper's own images, with a voiceover and music.

A story (stories/<name>.json) lists the scenes: what the narrator says (with *words* to highlight in the
captions) and which animated scene kind shows it (card, wipe, grid, dial, circle, mix, steps, end; see
fun.html). Voice, music, timing and encoding reuse the rest of this folder.

python multiagent/tiktok/fun.py multiagent/tiktok/stories/sphereedit.json
python multiagent/tiktok/fun.py <story> --stills        # a contact sheet of stills only (no frames, no encode)
"""
import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import prompts as P  # noqa: E402
import render as R  # noqa: E402
import tiktok  # noqa: E402

GAP = 0.2          # seconds between scenes
CAPTION_WORDS = 6  # words of a phrase on screen at once
IMAGE = re.compile(r"\.(png|jpe?g|webp|gif)$", re.I)


def resolve(v, base: Path):
    """Image paths in the story become file:// URIs (relative to the story's figures folder)."""
    if isinstance(v, str) and IMAGE.search(v) and not v.startswith(("file:", "data:", "http")):
        p = (base / v).resolve()
        if not p.is_file():
            raise FileNotFoundError(p)
        return p.as_uri()
    if isinstance(v, list):
        return [resolve(x, base) for x in v]
    if isinstance(v, dict):
        return {k: resolve(x, base) for k, x in v.items()}
    return v


def words(text: str) -> list[dict]:
    """Caption words; *word* is highlighted."""
    out = []
    for raw in text.split():
        hi = "*" in raw
        for w, end in R.caption_words(raw.replace("*", "")):
            out.append({"text": w, "end": end, "hi": hi})
    return out


def timeline(story: dict, clips: list[dict], base: Path) -> dict:
    scenes, chunks, t = [], [], 0.0
    n = len(story["scenes"])
    for i, (s, v) in enumerate(zip(story["scenes"], clips)):
        speech = t + (R.LEAD if i == 0 else 0.0)
        end = speech + v["length"] + (R.TAIL + 0.6 if i == n - 1 else GAP)
        scene = {k: resolve(x, base) for k, x in s.items() if k != "say"}
        scene.update(start=round(t, 3), end=round(end, 3))
        scenes.append(scene)
        ws = words(s["say"])
        times = R.word_times([(w["text"], w["end"]) for w in ws], speech, v["length"])
        j = 0
        while j < len(ws):   # a phrase builds up word by word, up to CAPTION_WORDS, breaking after punctuation
            k = j
            while k < len(ws) - 1 and k - j < CAPTION_WORDS - 1 and not ws[k]["end"]:
                k += 1
            chunks.append({"start": round(times[j][0], 3), "end": round(times[k][1] + 0.25, 3),
                           "words": [{"text": ws[m]["text"], "hi": ws[m]["hi"], "start": round(times[m][0], 3)}
                                     for m in range(j, k + 1)]})
            j = k + 1
        t = end
    for a, b in zip(chunks, chunks[1:]):
        a["end"] = min(a["end"], b["start"])
    return {"duration": round(t, 3), "cite": story.get("cite", ""), "scenes": scenes, "chunks": chunks}


def draw(tl: dict, out: Path, only: list[float] | None = None) -> list[Path]:
    """Screenshot every frame; the split layout as transparent PNGs (gameplay goes underneath)."""
    out.mkdir(parents=True, exist_ok=True)
    for old in [*out.glob("*.jpg"), *out.glob("*.png")]:
        old.unlink()
    split = tl.get("layout") == "split"
    times = only if only is not None else [i / R.FPS for i in range(int(tl["duration"] * R.FPS))]
    files = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--allow-file-access-from-files"])   # canvas reads the paper's images
        page = browser.new_page(viewport={"width": 1080, "height": 1920})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto((HERE / "fun.html").as_uri(), wait_until="networkidle")
        page.evaluate("tl => setup(tl)", tl)
        if errors:
            raise RuntimeError(f"fun.html: {errors[0]}")
        started = time.time()
        for i, t in enumerate(times):
            page.evaluate("t => frame(t)", t)
            if split:
                f = out / f"{i:05d}.png"
                page.screenshot(path=str(f), omit_background=True)
            else:
                f = out / f"{i:05d}.jpg"
                page.screenshot(path=str(f), type="jpeg", quality=90)
            files.append(f)
            if only is None and i % 150 == 0:
                print(f"    frame {i}/{len(times)} ({time.time() - started:.0f}s)")
        if errors:
            raise RuntimeError(f"fun.html: {errors[0]}")
        browser.close()
    return files


def main() -> None:
    load_dotenv(tiktok.ROOT / ".env")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("story", type=Path)
    ap.add_argument("--stills", action="store_true", help="only a contact sheet of stills (cheap preview)")
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--no-subway", action="store_true", help="skip the Subway Surfers version")
    ap.add_argument("--subway-only", action="store_true", help="only (re)make the Subway Surfers version of the last cut")
    ap.add_argument("--subway", help="gameplay video file or URL (default: assets/subway.mp4, downloaded once)")
    ap.add_argument("--music-level", type=float, default=0.16)
    ap.add_argument("--tts-model", default=tiktok.DEFAULTS["tts_model"])
    ap.add_argument("--voice", default=tiktok.DEFAULTS["voice"])
    ap.add_argument("--music-model", default=tiktok.DEFAULTS["music_model"])
    ap.add_argument("--out", type=Path, default=HERE / "outputs")
    args = ap.parse_args()

    story = json.loads(args.story.read_text())
    name = args.story.stem
    td = HERE / "work" / f"fun-{name}" / "take"
    td.mkdir(parents=True, exist_ok=True)
    base = (args.story.parent / story["figures"]).resolve()
    costs = tiktok.Costs()
    cfg = SimpleNamespace(tts_model=args.tts_model, voice=args.voice, music_model=args.music_model, no_music=args.no_music)

    P.SECONDS = story.get("seconds", 28)   # tiktok.voice speeds the voice up (at most 1.15x) to fit
    plan = {"voice_style": story["voice_style"], "music_prompt": story["music_prompt"],
            "scenes": [{"narration": s["say"].replace("*", "")} for s in story["scenes"]]}
    print(f"{name}: voicing {len(plan['scenes'])} scenes")
    clips = tiktok.voice(plan, td, cfg, costs)
    tl = timeline(story, clips, base)
    R.save_json(tl, td / "timeline.json")
    print(f"  {tl['duration']:.1f}s; voice match " + ", ".join(f"{c['match']:.2f}" for c in clips))

    if args.stills:
        mids = [s["start"] + 0.75 * (s["end"] - s["start"]) for s in tl["scenes"]]
        files = draw(tl, td / "stills", only=mids)
        sheet = R.contact_sheet(files, td / "stills.jpg", cols=min(4, len(files)), width=360)
        print(f"  stills: {sheet}")
        return

    out, k = args.out / f"{name}_fun.mp4", 2
    while out.exists() and not args.subway_only:   # never overwrite an earlier video
        out, k = args.out / f"{name}_fun{k}.mp4", k + 1
    if args.subway_only:   # the newest cut of this story
        out = max(args.out.glob(f"{name}_fun*.mp4"), key=lambda f: f.stat().st_mtime)
        if out.stem.endswith("__subway"):
            out = out.with_name(out.stem[: -len("__subway")] + ".mp4")
        subway(tl, td, out, args)
        return

    music = tiktok.music(plan, td, cfg, costs)
    starts = [s["start"] + (R.LEAD if i == 0 else 0.0) for i, s in enumerate(tl["scenes"])]
    R.voice_track([Path(c["wav"]) for c in clips], starts, tl["duration"], td / "voice.wav")
    R.mix(td / "voice.wav", music, tl["duration"], td / "audio.m4a", args.music_level)
    print(f"  drawing {int(tl['duration'] * R.FPS)} frames")
    draw(tl, td / "frames")
    args.out.mkdir(parents=True, exist_ok=True)
    R.encode(td / "frames", td / "audio.m4a", out)
    R.save_json({"story": str(args.story), "duration": tl["duration"], "costs": costs,
                 "voice_match": [c["match"] for c in clips]}, out.with_suffix(".json"))
    print(f"  wrote {out} ({out.stat().st_size // 1_000_000} MB, {tl['duration']:.1f}s); cost ${sum(costs.values()):.2f}")
    if not args.no_subway:
        subway(tl, td, out, args)


def subway(tl: dict, td: Path, out: Path, args) -> None:
    """The same cut with the scenes in the top half and Subway Surfers gameplay in the bottom half."""
    game = tiktok.subway_footage(SimpleNamespace(subway=args.subway))
    if not game:
        return
    split = {**tl, "layout": "split"}
    print(f"  drawing {int(split['duration'] * R.FPS)} frames for the Subway Surfers version")
    draw(split, td / "frames-split")
    offset = random.Random(out.name).uniform(0, max(0.0, R.duration(game) - 1))
    sub = out.with_name(out.stem + "__subway.mp4")
    R.encode_split(td / "frames-split", td / "audio.m4a", game, offset, split["duration"], sub)
    print(f"  wrote {sub} ({sub.stat().st_size // 1_000_000} MB)")


if __name__ == "__main__":
    main()
