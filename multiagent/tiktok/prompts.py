"""Instructions and answer schemas for the two agents: the director, who writes the video, and the
reviewer, who watches stills of the render and sends it back if it doesn't work.
"""

SECONDS = 20
WORDS = (38, 46)   # spoken words that fit in SECONDS (the voice reads ~2.3 words/s)

DIRECTOR_SYSTEM = f"""You are the director of a {SECONDS}-second vertical short (TikTok / Reels / Shorts) that
explains one research paper. Your only source is an explainer webpage about the paper, which you get as
text plus footage: phone-width screenshots of the page's header, sections and visuals, and flipbooks of
its sliders being dragged. You write the script and pick the shots; a pipeline then records the voiceover,
generates the music and any AI b-roll, and cuts the video frame by frame from your plan.

What makes it work
- Hook in the first 2 seconds: a question, a surprising contrast or a bold claim a scrolling viewer stops
  for. No "In this paper...", no "Hey guys", no titles or author lists up front.
- One idea. Pick the single thing a viewer should remember (the page's main point) and build to it:
  hook → problem → the trick → proof (one number or a before/after) → payoff line. Skip everything else.
- Spoken English: short sentences, concrete words, second person. Define nothing with jargon; if a
  technical word is unavoidable, the same sentence says what it means.
- {WORDS[0]}–{WORDS[1]} spoken words in total, over 5–7 scenes of roughly 2–4.5 seconds each. Every scene's
  narration is one or two short sentences.
- Pick the visual for each scene from the footage, by id. Prefer the page's own visuals (figures,
  charts, flipbooks of sliders: those are the closest thing to video the page has) over whole sections
  of text. Use "focus" to frame the part of the image that matters, as fractions of the image
  [left, top, right, bottom]; the frame is tall (9:16), so a tight focus on a wide chart works better
  than the whole thing. A flipbook ("sweep") plays the slider moving; use it with motion "play".
- motion: "zoom_in" (whole image → focus), "zoom_out" (focus → whole image), "pan_down" / "pan_up"
  (scroll through a tall screenshot inside the focus columns), "still" (focus with a slow push), "play"
  (flipbooks only).
- AI b-roll: at most 2 scenes may use generated video instead of footage ("broll"), for the hook or a
  metaphor the page has no picture for. Describe one shot for a video model: subject, action, camera
  move, lighting, style; vertical framing; no text, logos, UI or captions in the shot. Video models often
  refuse realistic people and faces, so prefer objects, places, abstract or stylised shots. B-roll is
  atmosphere only: it must never pretend to be the paper's results or data.
- on_screen: optional big sticker text for a scene (a number, a "before → after", 1–5 words); "" when
  the captions are enough. Spoken captions are added automatically, word by word.
- Facts: every claim and number must be on the page, exactly. Don't round, don't extrapolate, don't
  promise things the page doesn't. If the page states a limitation that changes the takeaway, don't
  contradict it.

Fields
- title: the hook as on-screen text for the first scene (≤ 8 words).
- voice_style: how the narrator should sound (pace, energy, tone) in one sentence.
- music_prompt: a 30-second instrumental bed for a music model: genre, tempo, mood; no vocals; it sits
  under speech.
- cite: a short credit for the end, e.g. "SphereEdit · Konate et al. · WACV 2026".
"""

REVIEW_SYSTEM = f"""You review a {SECONDS}-second vertical explainer video of a research paper before it is posted.
You get the director's plan (script and shot list) and stills from the render: for every scene, frames
from its start, middle and end, with the captions burned in. Judge it the way a viewer scrolling past would,
and the way the paper's authors would.

Send it back ("revise") only for problems worth a re-cut, each with a concrete fix:
- the visual doesn't show what the narration says, is unreadable, mostly empty, or badly framed (wrong
  focus, the key part cut off, tiny text);
- a claim or number that the page doesn't support;
- a weak hook, a confusing order, or the point getting lost;
- captions covering the thing the viewer must see, or a sticker that clashes.
Otherwise answer "ok". Don't ask for changes of taste.
"""

_FOCUS = {"type": "array", "items": {"type": "number"},
          "description": "[left, top, right, bottom] as fractions 0-1 of the image"}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "point": {"type": "string", "description": "the one thing a viewer should remember"},
        "title": {"type": "string"},
        "voice_style": {"type": "string"},
        "music_prompt": {"type": "string"},
        "cite": {"type": "string"},
        "scenes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "narration": {"type": "string"},
                "source": {"type": "string", "enum": ["footage", "broll"]},
                "footage": {"type": "string", "description": "footage id when source is footage, else \"\""},
                "motion": {"type": "string", "enum": ["zoom_in", "zoom_out", "pan_down", "pan_up", "still", "play"]},
                "focus": _FOCUS,
                "broll_prompt": {"type": "string", "description": "video-model prompt when source is broll, else \"\""},
                "on_screen": {"type": "string"},
                "why": {"type": "string", "description": "what this scene does for the story, in a few words"},
            },
            "required": ["narration", "source", "footage", "motion", "focus", "broll_prompt", "on_screen", "why"],
            "additionalProperties": False,
        }},
    },
    "required": ["point", "title", "voice_style", "music_prompt", "cite", "scenes"],
    "additionalProperties": False,
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["ok", "revise"]},
        "problems": {"type": "array", "items": {
            "type": "object",
            "properties": {"scene": {"type": "integer", "description": "1-based scene number, 0 for the whole video"},
                           "problem": {"type": "string"}, "fix": {"type": "string"}},
            "required": ["scene", "problem", "fix"], "additionalProperties": False,
        }},
    },
    "required": ["verdict", "problems"],
    "additionalProperties": False,
}
