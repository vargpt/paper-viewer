"""Cut the video: lay the scenes out on the voiceover's clock, draw every frame in a browser
(compose.html), mix the audio and encode with ffmpeg.
"""
import html
import json
import re
import subprocess
import wave
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
FPS = 30
GAP = 0.12     # seconds of silence between scenes
LEAD = 0.15    # before the first word
TAIL = 0.9     # after the last word
CHUNK = 3      # caption words on screen at once


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def duration(f: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


# ---- audio ---------------------------------------------------------------------------------------

def trim_silence(src: Path, dst: Path, threshold: int = 600) -> float:
    """Cut leading/trailing silence from a 16-bit mono WAV; return the new length in seconds."""
    with wave.open(str(src)) as w:
        rate, pcm = w.getframerate(), w.readframes(w.getnframes())
    samples = memoryview(pcm).cast("h")
    loud = [i for i in range(0, len(samples), 120) if abs(samples[i]) > threshold]
    a = max(0, loud[0] - int(0.03 * rate)) if loud else 0
    b = min(len(samples), loud[-1] + int(0.08 * rate)) if loud else len(samples)
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate), w.writeframes(samples[a:b].tobytes())
    return (b - a) / rate


def speed_up(src: Path, dst: Path, factor: float) -> None:
    ffmpeg("-i", str(src), "-filter:a", f"atempo={factor:.4f}", "-ar", "24000", "-ac", "1", str(dst))


def voice_track(clips: list[Path], starts: list[float], total: float, out: Path) -> None:
    """Place each scene's clip at its start time on one 24 kHz track."""
    rate = 24000
    track = bytearray(int(total * rate) * 2)
    for clip, start in zip(clips, starts):
        with wave.open(str(clip)) as w:
            pcm = w.readframes(w.getnframes())
        i = int(start * rate) * 2
        track[i:i + len(pcm)] = pcm[:len(track) - i]
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate), w.writeframes(bytes(track))


def mix(voice: Path, music: Path | None, total: float, out: Path, music_level: float = 0.14) -> None:
    if not music:
        ffmpeg("-i", str(voice), "-af", "loudnorm=I=-14:TP=-1.5", "-ar", "48000", "-c:a", "aac", "-b:a", "192k", str(out))
        return
    fade = max(0.0, total - 1.2)
    ffmpeg("-i", str(voice), "-stream_loop", "-1", "-i", str(music), "-filter_complex",
           f"[0:a]aresample=48000,pan=stereo|c0=c0|c1=c0[v];"
           f"[1:a]aresample=48000,atrim=0:{total:.3f},volume={music_level},afade=t=in:d=0.4,afade=t=out:st={fade:.3f}:d=1.2[m];"
           f"[v][m]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5[a]",
           "-map", "[a]", "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "192k", str(out))


# ---- timeline ------------------------------------------------------------------------------------

def caption_words(text: str) -> list[tuple[str, bool]]:
    """The words as captions show them (no trailing commas or full stops), and whether each ends a phrase."""
    out = []
    for w in text.split():
        w = re.sub(r"^[\"“‘'(]+|[\"”’')]+$", "", w)
        end = bool(re.search(r"[.?!:;,—]$", w))
        w = w.rstrip(".,;:—–-") if not w.endswith(("?", "!")) else w
        if w:
            out.append((w, end))
    return out


def word_times(words: list[tuple[str, bool]], start: float, length: float) -> list[tuple[float, float]]:
    """Spread the words over the clip by length (no forced alignment; good enough for 3-word chunks)."""
    weights = [len(w) + 3 + (4 if end else 0) for w, end in words]   # a little pause after a phrase
    total, t, out = sum(weights), start, []
    for wt in weights:
        d = length * wt / total
        out.append((t, t + d))
        t += d
    return out


def timeline(plan: dict, footage: dict, voice: list[dict], broll: dict[int, list[str]], layout: str) -> dict:
    """voice: per scene {"length": seconds, "said": transcript}. broll: scene index -> frame files."""
    items = {it["id"]: it for it in footage["items"]}
    base = Path(footage["dir"])
    scenes, chunks, t = [], [], 0.0
    for i, (s, v) in enumerate(zip(plan["scenes"], voice)):
        speech = (LEAD if i == 0 else 0.0) + t
        end = speech + v["length"] + (TAIL if i == len(plan["scenes"]) - 1 else GAP)
        scene = {"start": round(t, 3), "end": round(end, 3), "speech": round(speech, 3),
                 "on_screen": html.escape(s["on_screen"])}
        if i in broll:
            scene.update(kind="broll", frames=[Path(f).as_uri() for f in broll[i]])
        else:
            it = items[s["footage"]]
            frames = it.get("frames") if s["motion"] == "play" and it.get("frames") else [it["file"]]
            scene.update(kind="footage", frames=[(base / f).as_uri() for f in frames], size=it["size"],
                         motion=s["motion"], focus=s["focus"])
        scenes.append(scene)

        words = caption_words(v["said"])
        chunks += scene_chunks(words, word_times(words, speech, v["length"]))
        t = end
    for a, b in zip(chunks, chunks[1:]):
        a["end"] = b["start"] if b["start"] - a["start"] < 2.0 else a["start"] + 1.6
    if chunks:
        chunks[-1]["end"] = scenes[-1]["end"] - 0.3
    return {"layout": layout, "duration": round(t, 3), "title": html.escape(plan["title"]),
            "cite": html.escape(plan["cite"]), "scenes": scenes, "chunks": chunks}


def scene_chunks(words: list[tuple[str, bool]], times: list) -> list[dict]:
    """Up to CHUNK words at a time, cut early after a sentence end so captions follow the phrasing."""
    out, i = [], 0
    while i < len(words):
        n = min(CHUNK, len(words) - i)
        for k in range(n - 1):
            if words[i + k][1]:
                n = k + 1
                break
        group = list(zip(words, times))[i:i + n]
        out.append({"words": [{"text": html.escape(w), "start": round(a, 3)} for (w, _), (a, _) in group],
                    "start": round(group[0][1][0], 3)})
        i += n
    return out


# ---- frames and encoding -------------------------------------------------------------------------

def draw(tl: dict, out: Path, *, transparent: bool = False, only: list[float] | None = None,
         scale: float = 1.0) -> list[Path]:
    """Screenshot every frame (or just the times in `only`) into out/."""
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*"):
        old.unlink()
    times = only if only is not None else [i / FPS for i in range(int(tl["duration"] * FPS))]
    files = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=scale)
        page.goto((HERE / "compose.html").as_uri(), wait_until="networkidle")
        page.evaluate("tl => setup(tl)", tl)
        for i, t in enumerate(times):
            page.evaluate("t => frame(t)", t)
            f = out / (f"{i:05d}.png" if transparent else f"{i:05d}.jpg")
            if transparent:
                page.screenshot(path=str(f), omit_background=True)
            else:
                page.screenshot(path=str(f), type="jpeg", quality=92)
            files.append(f)
            if only is None and i % 150 == 0:
                print(f"    frame {i}/{len(times)}")
        browser.close()
    return files


def h264(crf: int = 18) -> list[str]:
    """libx264 when this ffmpeg has it, else OpenH264 (some conda builds ship only that) at a high bitrate."""
    encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if "libx264" in encoders:
        return ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf)]
    return ["-c:v", "libopenh264", "-b:v", "12M", "-maxrate", "16M"]


def encode(frames: Path, audio: Path, out: Path) -> None:
    ffmpeg("-framerate", str(FPS), "-i", str(frames / "%05d.jpg"), "-i", str(audio),
           *h264(18), "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(out))


def encode_split(frames: Path, audio: Path, gameplay: Path, offset: float, total: float, out: Path) -> None:
    """The transparent split-layout frames over gameplay in the bottom half."""
    ffmpeg("-stream_loop", "-1", "-ss", f"{offset:.2f}", "-t", f"{total + 0.5:.2f}", "-i", str(gameplay),   # loops a short clip
           "-framerate", str(FPS), "-i", str(frames / "%05d.png"), "-i", str(audio),
           "-filter_complex",
           f"[0:v]fps={FPS},scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
           f"crop=1080:960:0:800,pad=1080:1920:0:960:black,setsar=1[g];[g][1:v]overlay=0:0:format=auto,format=yuv420p[v]",
           "-map", "[v]", "-map", "2:a", *h264(19), "-r", str(FPS),
           "-c:a", "copy", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out))


def contact_sheet(files: list[Path], out: Path, cols: int = 6, width: int = 300) -> Path:
    """A grid of stills, for the reviewer."""
    rows = (len(files) + cols - 1) // cols
    inputs, filt = [], []
    for i, f in enumerate(files):
        inputs += ["-i", str(f)]
        filt.append(f"[{i}:v]scale={width}:-1[s{i}]")
    pad = cols * rows - len(files)
    layout = "|".join(f"{(i % cols) * width}_{(i // cols) * int(width * 16 / 9)}" for i in range(cols * rows))
    for k in range(pad):
        inputs += ["-f", "lavfi", "-i", f"color=black:s={width}x{int(width * 16 / 9)}:d=1"]
        filt.append(f"[{len(files) + k}:v]null[s{len(files) + k}]")
    filt.append("".join(f"[s{i}]" for i in range(cols * rows)) + f"xstack=inputs={cols * rows}:layout={layout}:fill=black[o]")
    ffmpeg(*inputs, "-filter_complex", ";".join(filt), "-map", "[o]", "-frames:v", "1", "-q:v", "3", str(out))
    return out


def save_json(obj, f: Path) -> None:
    f.write_text(json.dumps(obj, indent=2, ensure_ascii=False))
