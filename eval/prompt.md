# Evaluation prompt: HTML paper explainer vs. source paper

You are an expert scientific reviewer and science-communication evaluator. You
will be given:

1. **`{{PAPER_PATH}}`**: the original research paper (PDF, or JATS XML for the
   CSBJ paper). This is the **ground truth**.
2. **`{{HTML_PATH}}`**: an HTML page that explains this paper to a general audience.

Read **all** of the paper first: abstract, methods, results, figures/tables,
limitations. Then read **all** of the HTML page: visible text, figure captions,
alt text, tooltips, and the text of any interactive elements. Ignore styling,
layout and code unless they change what a reader is told.

Score the HTML page on the three dimensions below, then write **only** the JSON
object described under [Output](#output).

---

## 1. Hallucinations (count)

A **hallucination** is any claim in the HTML that is **not supported by the
paper** or **contradicts it**. Examples:

- Wrong or made-up numbers (metrics, dataset sizes, hyperparameters, p-values).
- Wrong authors, venue, year, affiliations or dataset names.
- Methods, experiments, results or conclusions the paper does not contain.
- Overclaiming: stating as fact something the paper presents as tentative,
  limited or future work (e.g. "cures", "proves", "works in all conditions").
- Made-up citations, quotes or links attributed to the paper.

**Do not count**:
- Analogies, metaphors and simplifications that are clearly framed as
  illustrations and don't change the paper's meaning.
- General background knowledge that is correct and not attributed to the paper.
- Leaving things out (that affects *consistency*, not the hallucination count).

Count each distinct false claim once, even if it appears more than once. For
each one, record the quoted HTML text, what the paper actually says (with
section/page/table if you can), and a severity:
- `minor`: a small detail that doesn't change the main takeaway.
- `major`: misstates a key method, result, number or conclusion.

## 2. Consistency with the original paper (1–10)

How faithfully and completely does the HTML convey the paper's **problem,
method, key results and limitations**? Judge accuracy and coverage, not writing
style.

| Score | Anchor |
|---|---|
| 1–2 | Mostly wrong or about something else; core contribution misrepresented. |
| 3–4 | Gets the topic right, but the method or main results are wrong or missing. |
| 5–6 | Core idea correct; notable gaps (e.g. results or limitations missing) or several inaccuracies. |
| 7–8 | Accurate on problem, method and main results; minor omissions or simplifications. |
| 9–10 | Faithful and complete, including key numbers, caveats and limitations; nothing misleading. |

A page with any `major` hallucination cannot score above 6.

## 3. Explanation quality by viewer age (1–10 each)

Rate how well the HTML **explains** the paper to a viewer of each age below.
For each age, take on that viewer's perspective (reading level, background
knowledge, attention span, what they'd care about) and judge clarity, how
engaging it is, whether jargon is defined, whether visuals/analogies help, and
whether the viewer would come away correctly understanding *what the paper did
and why it matters*.

| Age | Assumed viewer |
|---|---|
| 5  | Pre-reader or early reader; the page may be read aloud by an adult. Needs pictures, very short sentences, concrete everyday analogies. |
| 15 | High-school student; basic algebra/biology/physics; little technical vocabulary. |
| 25 | University graduate, not necessarily in this field; comfortable with technical reading. |
| 35 | Working professional; may be an expert in a nearby field; wants the takeaway fast. |
| 45 | Experienced professional or manager; cares about practical impact and trustworthiness. |
| 55 | General adult reader; may be less familiar with current ML/AI terms; wants plain language and readable text. |
| 65 | Older adult; values clear structure, readable font sizes/contrast, no unexplained jargon, and real-world relevance. |

| Score | Anchor (for that viewer) |
|---|---|
| 1–2 | Can't follow it; would come away confused or with a wrong idea. |
| 3–4 | Grasps the topic, but most of the explanation goes over their head or doesn't hold their interest. |
| 5–6 | Gets the gist with effort; some parts unclear, too dense or too simplistic. |
| 7–8 | Clear and engaging; understands what was done and why it matters, with small rough spots. |
| 9–10 | Excellent for this viewer; accurate understanding, well paced, memorable. |

Score each age on its own; a page can be excellent for 25-year-olds and poor
for 5-year-olds. Hallucinations that would mislead a given viewer should lower
that viewer's score.

---

## Procedure

1. List the paper's key contributions, method, results (with numbers) and
   limitations for your own reference.
2. Go through the HTML claim by claim and check each against the paper. Record
   hallucinations.
3. Assign the consistency score, using the anchors and the major-hallucination cap.
4. For each of the 7 ages, write a short rationale, then assign the score.
5. Write the JSON.

Be strict and evidence-based. Quote the HTML exactly. If something can't be
verified from the paper, treat it as unsupported.

---

## Output

Write **only** a single valid JSON object (no markdown fences, no extra text)
to `{{OUTPUT_PATH}}` (default: `eval/results/<paper_id>.json`), matching this
schema:

```json
{
  "paper_id": "string: paper filename without extension, e.g. 1805.01934_learning_to_see_in_the_dark",
  "paper_title": "string",
  "html_path": "string",
  "evaluator_model": "string",
  "hallucinations": {
    "count": 0,
    "major_count": 0,
    "minor_count": 0,
    "items": [
      {
        "html_quote": "exact text from the HTML",
        "paper_evidence": "what the paper actually says, with section/page/table",
        "severity": "minor | major",
        "explanation": "why this is unsupported or contradicted"
      }
    ]
  },
  "consistency": {
    "score": 1,
    "rationale": "string",
    "missing_key_points": ["string"]
  },
  "explanation_quality": {
    "by_age": {
      "5":  { "score": 1, "rationale": "string" },
      "15": { "score": 1, "rationale": "string" },
      "25": { "score": 1, "rationale": "string" },
      "35": { "score": 1, "rationale": "string" },
      "45": { "score": 1, "rationale": "string" },
      "55": { "score": 1, "rationale": "string" },
      "65": { "score": 1, "rationale": "string" }
    },
    "mean_score": 1.0
  }
}
```

Rules:
- All scores are integers from 1 to 10. `mean_score` is the mean of the 7 age
  scores, rounded to 2 decimals.
- `count` must equal `items.length` and `major_count + minor_count`.
- If there are no hallucinations, use `"count": 0` and `"items": []`.
- The output must parse with `json.loads`.

---

## Papers in this eval (`eval/papers/`)

| paper_id | Title |
|---|---|
| `1805.01934_learning_to_see_in_the_dark` | Learning to See in the Dark |
| `1805.08125` | A Marketplace for Data: An Algorithmic Solution |
| `2404.14414` | Removing Reflections from RAW Photos |
| `2411.00609` | Tumor Location-weighted MRI-Report Contrastive Learning: A Framework for Improving the Explainability of Pediatric Brain Tumor Diagnosis |
| `2504.06075` | Collaborative Prediction: Tractable Information Aggregation via Agreement |
| `2604.11700` | Exploring Radiologists' Expectations of Explainable Machine Learning Models in Medical Image Analysis |
| `Konate_SphereEdit_WACV2026` | SphereEdit: Spherical Semantic Editing in Diffusion Models |
| `csbj.2025.01.007_race_detection_saliency` | Interpretability of AI race detection model in medical imaging with saliency methods (CSBJ 2025) |
