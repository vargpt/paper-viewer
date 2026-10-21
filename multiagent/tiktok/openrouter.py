"""The OpenRouter calls the video needs: a vision LLM, text-to-speech, music and video generation.

Everything goes through the same OPENROUTER_API_KEY as the rest of paper-viewer, so the costs
show up on the same account. Each function returns its result plus the cost OpenRouter reported.
"""
import base64
import json
import os
import time
from pathlib import Path

import requests

API = "https://openrouter.ai/api/v1"


def _key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("LLM_API_KEY")
    if not key:
        raise SystemExit("no OPENROUTER_API_KEY (or LLM_API_KEY) in paper-viewer/.env")
    return key


def _headers() -> dict:
    return {"Authorization": f"Bearer {_key()}", "X-Title": "paper-viewer tiktok"}


def image_part(path: Path) -> dict:
    mime = "image/png" if path.suffix == ".png" else "image/jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"}}


def _post(path: str, body: dict, *, stream: bool = False, tries: int = 4) -> requests.Response:
    for attempt in range(tries):
        try:
            r = requests.post(f"{API}{path}", headers=_headers(), json=body, stream=stream, timeout=600)
            if r.status_code < 500 and r.status_code != 429:
                if r.status_code >= 400:
                    raise RuntimeError(f"OpenRouter {path} {r.status_code}: {r.text[:800]}")
                return r
            err = f"{r.status_code}: {r.text[:300]}"
        except requests.RequestException as e:
            err = str(e)
        print(f"    retrying {path} ({err})")
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"OpenRouter {path} failed after {tries} tries: {err}")


def chat_json(model: str, system: str, content: list, schema: dict, *, max_tokens: int = 16000,
              effort: str | None = "high") -> tuple[dict, float]:
    """One chat call whose answer must match `schema`."""
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": schema}},
    }
    if effort:
        body["reasoning"] = {"effort": effort}
    for attempt in range(3):
        data = _post("/chat/completions", body).json()
        if "error" in data:
            raise RuntimeError(f"OpenRouter chat error: {data['error']}")
        text = data["choices"][0]["message"].get("content") or ""
        cost = (data.get("usage") or {}).get("cost") or 0.0
        try:
            return json.loads(_strip_fence(text)), cost
        except json.JSONDecodeError:
            print(f"    answer was not valid JSON, asking again ({attempt + 1}/3)")
    raise RuntimeError("the model never returned valid JSON")


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return text


def _stream_audio(body: dict) -> tuple[bytes, str, float]:
    """Stream a chat completion with audio output; return the audio bytes, transcript and cost."""
    r = _post("/chat/completions", {**body, "modalities": ["text", "audio"], "stream": True}, stream=True)
    audio, transcript, cost = b"", "", 0.0
    for line in r.iter_lines():
        if not line.startswith(b"data: ") or line == b"data: [DONE]":
            continue
        chunk = json.loads(line[6:])
        if "error" in chunk:
            raise RuntimeError(f"OpenRouter audio error: {chunk['error']}")
        for choice in chunk.get("choices", []):
            a = choice.get("delta", {}).get("audio") or {}
            if a.get("data"):
                audio += base64.b64decode(a["data"])
            transcript += a.get("transcript") or ""
        if chunk.get("usage"):
            cost = chunk["usage"].get("cost") or 0.0
    if not audio:
        raise RuntimeError(f"{body['model']} returned no audio")
    return audio, transcript, cost


def speak(text: str, out: Path, *, model: str, voice: str, style: str) -> tuple[str, float]:
    """Text to speech into a 24 kHz mono WAV. Returns what the voice actually said, and the cost."""
    import wave
    system = ("You are a voice actor recording a voiceover. Read the user's text aloud exactly, word for word, "
              f"and say nothing else. Delivery: {style}")
    pcm, transcript, cost = _stream_audio({
        "model": model, "audio": {"voice": voice, "format": "pcm16"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
    })
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(pcm)
    return transcript.strip() or text, cost


def music(prompt: str, out: Path, *, model: str) -> float:
    """A ~30 s instrumental clip (MP3) from Lyria."""
    audio, _, cost = _stream_audio({"model": model, "messages": [{"role": "user", "content": prompt}]})
    out.write_bytes(audio)
    return cost


def video(prompt: str, out: Path, *, model: str, duration: int, resolution: str = "1080p",
          first_frame: Path | None = None, poll: int = 10, timeout: int = 1200) -> float:
    """Generate a vertical clip (no audio) and download it. Blocks until the job is done."""
    body = {"model": model, "prompt": prompt, "duration": duration, "resolution": resolution,
            "aspect_ratio": "9:16", "generate_audio": False}
    if first_frame:
        body["frame_images"] = [{**image_part(first_frame), "frame_type": "first_frame"}]
    job = _post("/videos", body).json()
    url, start = job.get("polling_url") or f"{API}/videos/{job['id']}", time.time()
    while True:
        time.sleep(poll)
        st = requests.get(url, headers=_headers(), timeout=60).json()
        if st.get("status") == "completed":
            break
        if st.get("status") in ("failed", "cancelled", "expired"):
            raise RuntimeError(f"video job {job.get('id')} {st.get('status')}: {st.get('error')}")
        if time.time() - start > timeout:
            raise RuntimeError(f"video job {job.get('id')} still {st.get('status')} after {timeout}s")
    content = (st.get("unsigned_urls") or [f"{API}/videos/{job['id']}/content?index=0"])[0]
    r = requests.get(content, headers=_headers(), timeout=300)
    r.raise_for_status()
    out.write_bytes(r.content)
    return (st.get("usage") or {}).get("cost") or 0.0
