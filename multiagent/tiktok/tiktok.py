"""Turn a generated paper webpage into a 20-second vertical short (TikTok / Reels / Shorts),
plus a split-screen version with Subway Surfers gameplay underneath.

    python multiagent/tiktok/tiktok.py eval/papers/Konate_SphereEdit_WACV2026.pdf   # finds the paper's page
    python multiagent/tiktok/tiktok.py path/to/page.html                             # or give the page
    python multiagent/tiktok/tiktok.py <paper or page> --take 1                       # resume / re-cut take 1

Every run without --take makes a new take (new script, new shots); the page is filmed once per page.
"""
import argparse
import concurrent.futures as cf
import difflib
import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent   # paper-viewer/
sys.path.insert(0, str(HERE))

from dotenv import load_dotenv  # noqa: E402

import openrouter as OR  # noqa: E402
import prompts as P  # noqa: E402
import render as R  # noqa: E402
from capture import capture  # noqa: E402

DEFAULTS = {
    "model": "anthropic/claude-opus-5.5",
    "tts_model": "openai/gpt-audio",
    "voice": "ash",
    "music_model": "google/lyria-3-clip-preview",
    "video_model": "google/veo-3.1-fast",
    "video_fallback": "kwaivgi/kling-v3.0-std",
}
MAX_SPEEDUP = 1.15   # faster than this and the voice sounds sped up; the director cuts words instead
SUBWAY_URL = "https://www.youtube.com/watch?v=hJcv2nZ8x84"   # "Subway Surfers Gameplay No Copyright - Vertical"


def find_page(target: Path) -> Path:
    """A page, or the newest page made for this paper: multi-agent outputs first, then the baselines."""
    if target.suffix == ".html":
        return target
    stem = target.stem
    for d in (HERE.parent / "outputs", ROOT / "baseline" / "outputs"):
        pages = sorted(d.rglob(f"{stem}__*.html"), key=lambda f: f.stat().st_mtime, reverse=True)
        if pages:
            return pages[0]
    raise SystemExit(f"no generated page for {stem} in multiagent/outputs or baseline/outputs; run the pipeline first")


def h(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:10]


class Tee:
    def __init__(self, stream, path: Path):
        self.stream, self.file = stream, open(path, "a", buffering=1)

    def write(self, text: str) -> None:
        self.stream.write(text)
        self.file.write(text)

    def flush(self) -> None:
        self.stream.flush()
        self.file.flush()


class Costs(dict):
    def add(self, what: str, usd: float) -> None:
        self[what] = round(self.get(what, 0.0) + (usd or 0.0), 4)


# ---- 1. film the page ----------------------------------------------------------------------------

def film(page: Path, ws: Path) -> dict:
    d = ws / "footage"
    key = h(str(page.resolve()), page.stat().st_mtime)
    done = d / "footage.json"
    if done.exists() and json.loads(done.read_text()).get("key") == key:
        footage = json.loads(done.read_text())
    else:
        print(f"filming {page.name} ...")
        if d.exists():
            shutil.rmtree(d)
        footage = capture(page, d)
        footage["key"] = key
        R.save_json(footage, done)
    footage["dir"] = str(d)
    thumbs = d / "thumbs"
    thumbs.mkdir(exist_ok=True)
    for it in footage["items"]:
        th = thumbs / f"{it['id']}.jpg"
        if th.exists():
            continue
        if it["type"] == "sweep":   # first, middle and last frame side by side
            fr = [d / it["frames"][i] for i in (0, len(it["frames"]) // 2, -1)]
            R.ffmpeg(*sum((["-i", str(f)] for f in fr), []), "-filter_complex",
                     "[0]scale=-2:420[a];[1]scale=-2:420[b];[2]scale=-2:420[c];[a][b][c]hstack=3", "-q:v", "4", str(th))
        else:
            R.ffmpeg("-i", str(d / it["file"]), "-vf", "scale=360:-1", "-q:v", "4", str(th))
    return footage


# ---- 2. the director -----------------------------------------------------------------------------

def footage_content(footage: dict) -> list:
    content = [{"type": "text", "text": f"The webpage's text:\n<page>\n{footage['text'][:14000]}\n</page>\n\n"
                                       "The footage you can use, each with its id, what it is and its size in pixels:"}]
    for it in footage["items"]:
        w, hgt = it["size"]
        note = " (thumbnail shows its first, middle and last frame)" if it["type"] == "sweep" else ""
        content.append({"type": "text", "text": f"\nid: {it['id']} — {it['about']} — {w}x{hgt}px{note}"})
        content.append(OR.image_part(Path(footage["dir"]) / "thumbs" / f"{it['id']}.jpg"))
    return content


def direct(footage: dict, model: str, costs: Costs, feedback: str = "") -> dict:
    content = footage_content(footage)
    content.append({"type": "text", "text": feedback or "\nWrite the plan for the video."})
    plan, cost = OR.chat_json(model, P.DIRECTOR_SYSTEM, content, P.PLAN_SCHEMA)
    costs.add("director", cost)
    return sanitize(plan, footage)


def sanitize(plan: dict, footage: dict) -> dict:
    items = {it["id"]: it for it in footage["items"]}
    if plan["scenes"] and plan["scenes"][0]["on_screen"].strip().lower() == plan["title"].strip().lower():
        plan["scenes"][0]["on_screen"] = ""   # the title is already on screen
    brolls = 0
    for s in plan["scenes"]:
        if s["source"] == "broll" and s["broll_prompt"].strip() and brolls < 2:
            brolls += 1
            continue
        s["source"] = "footage"
        if s["footage"] not in items:
            s["footage"] = "screen"
        f = [min(1.0, max(0.0, float(x))) for x in (s["focus"] + [0, 0, 1, 1])[:4]]
        s["focus"] = f if f[2] - f[0] > 0.05 and f[3] - f[1] > 0.02 else [0, 0, 1, 1]
        is_sweep = items[s["footage"]]["type"] == "sweep"
        if is_sweep:
            s["motion"] = "play"
        elif s["motion"] == "play":
            s["motion"] = "still"
    return plan


def review(plan: dict, stills: Path, model: str, costs: Costs) -> dict:
    content = [{"type": "text", "text": "The plan:\n" + json.dumps(plan, indent=2, ensure_ascii=False)},
               {"type": "text", "text": "Stills from the render, three per scene (start, middle, end), in order, left to right and top to bottom:"},
               OR.image_part(stills)]
    verdict, cost = OR.chat_json(model, P.REVIEW_SYSTEM, content, P.REVIEW_SCHEMA, max_tokens=6000)
    costs.add("reviewer", cost)
    return verdict


# ---- 3. voice, music, b-roll ---------------------------------------------------------------------

def voice(plan: dict, td: Path, cfg, costs: Costs) -> list[dict]:
    """One clip per scene (so scene timing is exact), trimmed; sped up together if the total runs long."""
    vd = td.parent / "voice"   # shared by the takes, keyed by text and voice
    vd.mkdir(exist_ok=True)
    style = plan["voice_style"]

    def one(text: str) -> dict:
        key = h(text, cfg.tts_model, cfg.voice, style)
        meta = vd / f"{key}.json"
        if meta.exists():
            return json.loads(meta.read_text())
        best = None
        for attempt in range(3):   # the voice model sometimes paraphrases; keep the closest read
            said, cost = OR.speak(text, vd / f"{key}.raw.wav", model=cfg.tts_model, voice=cfg.voice, style=style)
            costs.add("voice", cost)
            sim = difflib.SequenceMatcher(None, norm(text), norm(said)).ratio()
            if not best or sim > best[0]:
                best = (sim, said)
                shutil.copy(vd / f"{key}.raw.wav", vd / f"{key}.best.wav")
            if sim > 0.9:
                break
        length = R.trim_silence(vd / f"{key}.best.wav", vd / f"{key}.wav")
        out = {"wav": str(vd / f"{key}.wav"), "length": length, "said": best[1], "match": round(best[0], 3)}
        R.save_json(out, meta)
        return out

    with cf.ThreadPoolExecutor(6) as pool:
        clips = list(pool.map(one, [s["narration"] for s in plan["scenes"]]))
    total = sum(c["length"] for c in clips) + R.GAP * (len(clips) - 1) + R.LEAD + R.TAIL
    limit = P.SECONDS + 0.5
    if total > limit:
        factor = min(MAX_SPEEDUP, (total - R.LEAD - R.TAIL) / (limit - R.LEAD - R.TAIL))
        print(f"  voiceover runs {total:.1f}s; speeding it up {factor:.2f}x")
        for c in clips:
            fast = Path(c["wav"]).with_suffix(f".x{factor:.2f}.wav")
            if not fast.exists():
                R.speed_up(Path(c["wav"]), fast, factor)
            c["wav"], c["length"] = str(fast), R.duration(fast)
    return clips


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower())


def music(plan: dict, td: Path, cfg, costs: Costs) -> Path | None:
    if cfg.no_music:
        return None
    f = td.parent / "music" / f"{h(plan['music_prompt'], cfg.music_model)}.mp3"
    f.parent.mkdir(exist_ok=True)
    if not f.exists():
        try:
            costs.add("music", OR.music(plan["music_prompt"], f, model=cfg.music_model))
        except Exception as e:
            print(f"  no music ({e})")
            return None
    return f


def video_durations(model: str) -> list[int]:
    import requests
    try:
        for m in requests.get(f"{OR.API}/videos/models", timeout=30).json()["data"]:
            if m["id"] == model:
                return sorted(m.get("supported_durations") or [5])
    except Exception:
        pass
    return [4, 6, 8]


def broll(plan: dict, clips: list[dict], td: Path, cfg, costs: Costs) -> dict[int, list[str]]:
    """Generate the b-roll scenes and cut each into frames. Scene index -> frame files."""
    wanted = [i for i, s in enumerate(plan["scenes"]) if s["source"] == "broll"]
    if not wanted:
        return {}
    durations = video_durations(cfg.video_model)
    bd = td.parent / "broll"
    bd.mkdir(exist_ok=True)

    def one(i: int):
        s = plan["scenes"][i]
        need = clips[i]["length"] + R.GAP + 0.3
        dur = next((d for d in durations if d >= need), durations[-1])
        key = h(s["broll_prompt"], cfg.video_model, dur)
        mp4, frames = bd / f"{key}.mp4", bd / key
        if not mp4.exists():
            print(f"  b-roll for scene {i + 1} ({dur}s, {cfg.video_model}) ...")
            try:
                costs.add("b-roll", OR.video(s["broll_prompt"], mp4, model=cfg.video_model, duration=dur))
            except Exception as e:   # often a content filter; one more go with another model
                print(f"  b-roll for scene {i + 1} failed ({e}); trying {cfg.video_fallback}")
                fd = video_durations(cfg.video_fallback)
                costs.add("b-roll", OR.video(s["broll_prompt"], mp4, model=cfg.video_fallback,
                                             duration=next((d for d in fd if d >= need), fd[-1])))
        if not frames.exists():
            frames.mkdir()
            R.ffmpeg("-i", str(mp4), "-vf", f"fps={R.FPS},scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                     "-q:v", "3", str(frames / "%04d.jpg"))
        # play the clip at its own speed from the start; only stretch it when it is shorter than the scene
        files = sorted(str(f) for f in frames.glob("*.jpg"))
        n = max(1, int(round(need * R.FPS)))
        return i, files[:n] if len(files) >= n else files

    out = {}
    with cf.ThreadPoolExecutor(2) as pool:
        for fut in [pool.submit(one, i) for i in wanted]:
            try:
                i, files = fut.result()
                out[i] = files
            except Exception as e:   # a failed generation falls back to the page's header
                print(f"  b-roll failed ({e}); using footage instead")
    for i in wanted:
        if i not in out:
            s = plan["scenes"][i]
            s.update(source="footage", footage="screen", motion="still", focus=[0, 0, 1, 1])
    return out


# ---- 4. cut --------------------------------------------------------------------------------------

def cut(plan: dict, footage: dict, td: Path, cfg, costs: Costs, layout: str = "full") -> tuple[dict, list[dict], Path]:
    with cf.ThreadPoolExecutor(2) as pool:
        mfut = pool.submit(music, plan, td, cfg, costs)
        clips = voice(plan, td, cfg, costs)
        rolls = broll(plan, clips, td, cfg, costs)
        mus = mfut.result()
    tl = R.timeline(plan, footage, clips, rolls, layout)
    starts = [s["speech"] for s in tl["scenes"]]
    R.voice_track([Path(c["wav"]) for c in clips], starts, tl["duration"], td / "voice.wav")
    R.mix(td / "voice.wav", mus, tl["duration"], td / "audio.m4a", cfg.music_level)
    R.save_json(tl, td / f"timeline-{layout}.json")
    return tl, clips, td / "audio.m4a"


def stills(tl: dict, td: Path) -> Path:
    times = []
    for s in tl["scenes"]:
        a, b = s["start"] + 0.25, s["end"] - 0.1
        times += [a, (a + b) / 2, b]
    files = R.draw(tl, td / "stills", only=times, scale=0.5)
    return R.contact_sheet(files, td / "stills.jpg", cols=6, width=300)


def subway_footage(cfg) -> Path | None:
    if cfg.subway and Path(cfg.subway).exists():
        return Path(cfg.subway)
    f = HERE / "assets" / "subway.mp4"
    if f.exists():
        return f
    url = cfg.subway or SUBWAY_URL
    print(f"  downloading gameplay from {url} (cached in assets/) ...")
    f.parent.mkdir(exist_ok=True)
    try:
        subprocess.run([sys.executable, "-m", "yt_dlp", "--no-playlist", "-q", "-f", "bv*[height<=1920][ext=mp4]/bv*",
                        "--download-sections", "*60-180", "-o", str(f), url], check=True)
        return f
    except Exception as e:
        print(f"  could not get gameplay ({e}); pass --subway path/to/gameplay.mp4")
        return None


def main() -> None:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", type=Path, help="a generated page (.html), or a paper (.pdf / name) to find its page")
    ap.add_argument("--take", type=int, help="resume or re-cut this take instead of making a new one")
    ap.add_argument("--replan", action="store_true", help="with --take: write a new script for that take")
    ap.add_argument("--review-rounds", type=int, default=1, help="times the reviewer may send the cut back")
    ap.add_argument("--no-subway", action="store_true", help="skip the Subway Surfers version")
    ap.add_argument("--subway", help="gameplay video file or URL (default: assets/subway.mp4, downloaded once)")
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--music-level", type=float, default=0.14)
    for k, v in DEFAULTS.items():
        ap.add_argument(f"--{k.replace('_', '-')}", default=v)
    ap.add_argument("--out", type=Path, default=HERE / "outputs")
    ap.add_argument("--work", type=Path, default=HERE / "work")
    cfg = ap.parse_args()

    page = find_page(cfg.target)
    # baseline and multi-agent pages share file names
    stem = page.stem + ("__baseline" if "baseline" in page.resolve().parts else "")
    ws = (cfg.work / stem).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    takes = sorted(int(m.group(1)) for d in ws.glob("take*") if (m := re.fullmatch(r"take(\d+)", d.name)))
    take = cfg.take or (takes[-1] + 1 if takes else 1)
    td = ws / f"take{take}"
    td.mkdir(exist_ok=True)
    sys.stdout = Tee(sys.stdout, td / "run.log")
    print(f"{stem} take {take}  (page: {page}, workspace: {td})")
    t0, costs = time.time(), Costs()
    if (td / "costs.json").exists():
        costs.update(json.loads((td / "costs.json").read_text()))

    footage = film(page, ws)
    plan_f = td / "plan.json"
    if plan_f.exists() and not cfg.replan:
        plan = json.loads(plan_f.read_text())
    else:
        print(f"directing ({cfg.model}) ...")
        plan = direct(footage, cfg.model, costs)
        R.save_json(plan, plan_f)
    show(plan)

    # cut, let the reviewer look at stills, re-cut
    reviews = json.loads((td / "reviews.json").read_text()) if (td / "reviews.json").exists() else []
    trims = 0
    while True:
        tl, clips, audio = cut(plan, footage, td, cfg, costs)
        if tl["duration"] > P.SECONDS + 1.5 and trims < 2:   # too long even sped up: shorter script
            trims += 1
            words = sum(len(s["narration"].split()) for s in plan["scenes"])
            keep = int(words * (P.SECONDS - 0.5) / tl["duration"])
            print(f"  {tl['duration']:.1f}s is too long; asking for {keep} words instead of {words}")
            plan = direct(footage, cfg.model, costs, feedback=(
                "\nYour previous plan:\n" + json.dumps(plan, indent=2, ensure_ascii=False) +
                f"\n\nRead aloud it runs {tl['duration']:.1f} s, not {P.SECONDS}. Rewrite the narration to at most "
                f"{keep} words in total; keep the shots and the story, cut words."))
            R.save_json(plan, plan_f)
            show(plan)
            continue
        if len(reviews) >= cfg.review_rounds or (reviews and reviews[-1]["verdict"] == "ok"):
            break
        print("reviewing stills ...")
        verdict = review(plan, stills(tl, td), cfg.model, costs)
        reviews.append(verdict)
        R.save_json(reviews, td / "reviews.json")
        R.save_json(costs, td / "costs.json")
        if verdict["verdict"] == "ok":
            print("  reviewer: ok")
            break
        notes = "\n".join(f"- scene {p['scene']}: {p['problem']} → {p['fix']}" for p in verdict["problems"])
        print(f"  reviewer sent it back:\n{notes}")
        print("re-directing ...")
        shutil.copy(plan_f, td / f"plan-r{len(reviews)}.json")
        plan = direct(footage, cfg.model, costs, feedback=(
            "\nYour previous plan:\n" + json.dumps(plan, indent=2, ensure_ascii=False) +
            "\n\nA reviewer watched the render and sent it back:\n" + notes +
            "\n\nWrite the revised plan. Keep what works; fix these problems."))
        R.save_json(plan, plan_f)
        show(plan)

    cfg.out.mkdir(parents=True, exist_ok=True)
    out = cfg.out / f"{stem}__take{take}.mp4"
    print(f"drawing {int(tl['duration'] * R.FPS)} frames ...")
    R.draw(tl, td / "frames")
    R.encode(td / "frames", audio, out)
    print(f"  wrote {out}")
    outputs = {"video": str(out)}

    if not cfg.no_subway:
        game = subway_footage(cfg)
        if game:
            split = R.timeline(plan, footage, clips, broll(plan, clips, td, cfg, costs), "split")
            R.save_json(split, td / "timeline-split.json")
            print("drawing the split-screen frames ...")
            R.draw(split, td / "frames-split", transparent=True)
            offset = random.Random(take).uniform(0, max(0.0, R.duration(game) - split["duration"] - 1))
            sub = cfg.out / f"{stem}__take{take}__subway.mp4"
            R.encode_split(td / "frames-split", audio, game, offset, split["duration"], sub)
            print(f"  wrote {sub}")
            outputs["subway"] = str(sub)

    for d in ("frames", "frames-split", "stills"):   # the frames are big and cheap to redraw
        shutil.rmtree(td / d, ignore_errors=True)
    R.save_json(costs, td / "costs.json")
    meta = {"page": str(page), "take": take, "duration_s": tl["duration"], "outputs": outputs,
            "models": {k: getattr(cfg, k) for k in DEFAULTS}, "cost_usd": costs, "cost_total_usd": round(sum(costs.values()), 4),
            "minutes": round((time.time() - t0) / 60, 1), "reviews": reviews, "plan": plan,
            "voice_match": [c["match"] for c in clips]}
    R.save_json(meta, cfg.out / f"{stem}__take{take}.json")
    print(f"done in {meta['minutes']} min, ${meta['cost_total_usd']:.2f}: {json.dumps(costs)}")


def show(plan: dict) -> None:
    words = sum(len(s["narration"].split()) for s in plan["scenes"])
    print(f"  \"{plan['title']}\" — {len(plan['scenes'])} scenes, {words} words")
    for i, s in enumerate(plan["scenes"], 1):
        src = f"b-roll: {s['broll_prompt'][:60]}…" if s["source"] == "broll" else f"{s['footage']} {s['motion']}"
        print(f"   {i}. {s['narration']}  [{src}]")


if __name__ == "__main__":
    main()
