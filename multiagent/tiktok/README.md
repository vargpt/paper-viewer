# Paper → 20-second short

Takes a page the pipeline generated and cuts a 20-second vertical video of it (1080×1920, 30 fps,
voiceover, music, word-by-word captions), in the TikTok / Reels / Shorts format. It also makes a
second version with Subway Surfers gameplay in the bottom half, as a joke.

```
generated page (.html)
  │
  ▼
film ─── a phone-width browser screenshots the header, every section and visual, and drags every slider
  │      (flipbooks: the closest thing to video the page has)
  ▼
director (Opus) ─── looks at the footage and page text; writes a hook → problem → trick → proof → payoff
  │                 script of ~55 words in 5–7 scenes, picks a shot, framing and camera move per scene,
  │                 and up to 2 AI b-roll shots
  ▼
voice (gpt-audio), music (Lyria), b-roll (Veo 3.1 Fast)  ── all through OpenRouter
  │
  ▼
cut ─── scenes on the voiceover's clock, every frame drawn in a browser (compose.html), ffmpeg mix and encode
  │
  ▼
reviewer (Opus) ─── looks at stills of every scene; "ok", or sends it back to the director with fixes
  │
  ▼
outputs/<page>__take<k>.mp4   and   outputs/<page>__take<k>__subway.mp4
```

## Run

From `paper-viewer/`:

```bash
pip install -r multiagent/tiktok/requirements.txt && playwright install chromium   # once; also needs ffmpeg

python multiagent/tiktok/tiktok.py eval/papers/Konate_SphereEdit_WACV2026.pdf   # newest page for this paper
python multiagent/tiktok/tiktok.py path/to/page.html                             # a specific page
```

Each run is a new **take** (a new script and cut) of the same page; the page is filmed only once per
page (again if the page changes). Takes land in `outputs/` next to a `.json` with the script, the
reviewer's notes and the cost.

```bash
python multiagent/tiktok/tiktok.py <paper> --take 2             # resume take 2, or re-cut it after editing work/.../take2/plan.json
python multiagent/tiktok/tiktok.py <paper> --take 2 --replan    # new script for take 2
```

To change the script by hand, edit `work/<page>/take<k>/plan.json` and re-run with `--take k`: only
what changed is regenerated (voice clips, music and b-roll are cached by their text).

| option | default | |
|---|---|---|
| `--model` | `anthropic/claude-opus-5.5` | director and reviewer |
| `--tts-model` / `--voice` | `openai/gpt-audio` / `ash` | voiceover (other voices: alloy, ballad, coral, echo, sage, shimmer, verse, marin, cedar) |
| `--music-model` | `google/lyria-3-clip-preview` | 30 s instrumental; `--no-music`, `--music-level 0.14` |
| `--video-model` | `google/veo-3.1-fast` | b-roll; any model from `https://openrouter.ai/api/v1/videos/models` with 9:16 |
| `--review-rounds` | `1` | times the reviewer may send it back (`0` skips the review) |
| `--subway` | `assets/subway.mp4` | gameplay file or URL; the default is downloaded once with yt-dlp; `--no-subway` skips that version |

The page is chosen as: the `.html` you pass, or else the newest `multiagent/outputs/<paper>__*.html`,
or else the newest baseline page for that paper (its outputs get a `__baseline` suffix).

**Cost.** About $1–3 per take: the director and reviewer calls (~$0.3–0.6 each with all the
screenshots), voice (~$0.05), music ($0.04) and b-roll ($0.10/s with Veo 3.1 Fast, so up to ~$1.60 for
two 8 s shots). The `.json` has the breakdown OpenRouter reported. Takes about 5–10 minutes, most of
it the b-roll and drawing ~600 frames twice.

## Files

- `tiktok.py`: the pipeline and CLI.
- `prompts.py`: the director's and reviewer's instructions and answer schemas. Tune the style here.
- `capture.py`: filming the page (viewport, what counts as a visual, slider flipbooks).
- `compose.html`: one frame of the video: layouts, camera moves, captions, title, sticker, credit.
- `render.py`: timing, audio mix, frame drawing, ffmpeg.
- `openrouter.py`: chat, speech, music and video generation calls.
- `work/<page>/` (git-ignored): `footage/` (screenshots, thumbnails, page text), `voice/`, `music/`,
  `broll/` (cached generations), `take<k>/` (`plan.json`, `reviews.json`, `timeline-*.json`, audio).

## Limits

- Caption timing is spread over each scene's clip by word length, not force-aligned, so it can drift
  by a word in long sentences. Each scene is voiced separately, so scene cuts are always on time.
- The voice model occasionally paraphrases; the read closest to the script (of up to 3) is used and
  the captions follow what was actually said. `voice_match` in the `.json` shows how close each was.
- B-roll is atmosphere only (the director is told never to fake results); a failed generation falls
  back to a shot of the page.
- The gameplay clip is a "no copyright" upload from YouTube; check its terms before posting anything
  publicly.
