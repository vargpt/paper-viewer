"""Turn a paper (PDF, or JATS XML) into files the agents can read.

Everything goes into the run's workspace:
    paper.md            full text, with "=== Page N ===" markers
    pages/page-NN.png   one image per page, for agents that want to look at a page
    figures/figN.png    each captioned figure, cropped from its page
    figures.json        figure id, file, page and caption
"""
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

PAGE_DPI = 100
FIGURE_ZOOM = 2.5  # 180 dpi crops
MAX_FIGURE_PX = 1800
CAPTION = re.compile(r"^\s*(?:fig\.?|figure)\s*(\d+)\s*[.:|]", re.I)
ANY_CAPTION = re.compile(r"^\s*(?:fig\.?|figure|tab\.?|table)\s*\d+", re.I)


@dataclass
class Paper:
    source: str
    kind: str  # "pdf" or "xml"
    text: str
    pages: int = 0
    figures: list[dict] = field(default_factory=list)


def load_paper(src: Path, ws: Path) -> Paper:
    """Extract the paper into the workspace once; later calls reuse the files."""
    meta = ws / "paper.json"
    if meta.exists():
        return Paper(text=(ws / "paper.md").read_text(), **json.loads(meta.read_text()))
    ws.mkdir(parents=True, exist_ok=True)
    paper = _from_pdf(src, ws) if src.suffix.lower() == ".pdf" else _from_xml(src)
    (ws / "paper.md").write_text(paper.text)
    (ws / "figures.json").write_text(json.dumps(paper.figures, indent=2))
    data = asdict(paper)
    data.pop("text")
    meta.write_text(json.dumps(data, indent=2))
    return paper


def page_image(i: int, pages: int) -> str:
    return f"pages/page-{i:0{max(2, len(str(pages)))}d}.png"


def _from_pdf(src: Path, ws: Path) -> Paper:
    doc = pymupdf.open(src)
    (ws / "pages").mkdir(exist_ok=True)
    (ws / "figures").mkdir(exist_ok=True)
    texts = []
    for i, page in enumerate(doc, 1):
        texts.append(f"=== Page {i} ===\n{page.get_text('text').strip()}")
        page.get_pixmap(dpi=PAGE_DPI).save(ws / page_image(i, doc.page_count))
    figures = _crop_figures(doc, ws / "figures")
    return Paper(source=str(src), kind="pdf", text="\n\n".join(texts), pages=doc.page_count, figures=figures)


def _from_xml(src: Path) -> Paper:
    # JATS XML: drop the reference list, keep every other piece of text.
    root = ET.parse(src).getroot()
    for parent in root.iter():
        for child in list(parent):
            if child.tag.split("}")[-1] == "ref-list":
                parent.remove(child)
    text = "\n".join(t.strip() for t in root.itertext() if t.strip())
    return Paper(source=str(src), kind="xml", text=text)


def _crop_figures(doc: pymupdf.Document, out: Path) -> list[dict]:
    """Crop every captioned figure: the drawings and images between its caption and the text above it."""
    figures, seen = [], set()
    for pno, page in enumerate(doc, 1):
        blocks = _text_blocks(page)
        if not blocks:
            continue
        graphics = _graphics(page)
        left, right = min(b.rect.x0 for b in blocks), max(b.rect.x1 for b in blocks)
        mid = (left + right) / 2
        two_columns = sum(1 for b in blocks if b.paragraph and b.rect.width < 0.5 * (right - left)) >= 2
        captions = [b.rect for b in blocks if b.caption]
        for b in blocks:
            m = CAPTION.match(b.text)
            if not m or m.group(1) in seen:
                continue
            cap = b.rect
            if not two_columns or cap.width > 0.55 * (right - left):
                band, clamp = (left - 8, right + 8), False
            elif (cap.x0 + cap.x1) / 2 < mid:
                band, clamp = (left - 8, mid), True
            else:
                band, clamp = (mid, right + 8), True
            x0, x1 = band
            for other in captions:  # side-by-side figures: split the band between their captions
                if other != cap and min(cap.y1, other.y1) - max(cap.y0, other.y0) > 0.5 * min(cap.height, other.height):
                    if other.x0 >= cap.x1 - 5:
                        x1, clamp = min(x1, (cap.x1 + other.x0) / 2), True
                    elif other.x1 <= cap.x0 + 5:
                        x0, clamp = max(x0, (other.x1 + cap.x0) / 2), True
            region = _region_above(page, cap, (x0, x1), clamp, blocks, graphics)
            if region is None:
                continue
            seen.add(m.group(1))
            zoom = min(FIGURE_ZOOM, MAX_FIGURE_PX / region.width)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=region)
            name = f"fig{m.group(1)}.png"
            pix.save(out / name)
            figures.append({
                "id": f"fig{m.group(1)}",
                "file": f"figures/{name}",
                "page": pno,
                "caption": " ".join(b.text.split()),
                "width": pix.width,
                "height": pix.height,
            })
    return figures


@dataclass
class _Block:
    rect: pymupdf.Rect
    text: str
    caption: bool  # starts like "Figure 3." or "Table 2"
    paragraph: bool  # running text in the body font, not a label inside a figure


def _text_blocks(page: pymupdf.Page) -> list[_Block]:
    raw = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        spans = [s for line in b["lines"] for s in line["spans"] if s["text"].strip()]
        if not spans:
            continue
        text = "\n".join("".join(s["text"] for s in line["spans"]) for line in b["lines"])
        chars = sum(len(s["text"]) for s in spans)
        size = sum(s["size"] * len(s["text"]) for s in spans) / chars
        raw.append((pymupdf.Rect(b["bbox"]), text, size, chars))
    if not raw:
        return []
    weight: dict[float, int] = {}
    for _, _, size, chars in raw:
        weight[round(size * 2) / 2] = weight.get(round(size * 2) / 2, 0) + chars
    body = max(weight, key=weight.get)
    return [_Block(rect, text, bool(ANY_CAPTION.match(text)),
                   abs(size - body) <= 0.6 and len(text.split()) >= 15 and rect.width >= 120)
            for rect, text, size, _ in raw]


def _graphics(page: pymupdf.Page) -> list[pymupdf.Rect]:
    rects = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    try:
        rects += [pymupdf.Rect(r) for r in page.cluster_drawings()]
    except Exception:  # older PyMuPDF, or a page whose drawings can't be clustered
        pass
    area = page.rect.get_area()
    return [r for r in rects if r.width >= 8 and r.height >= 8 and r.get_area() < 0.9 * area]


def _region_above(page, cap, band, clamp, blocks, graphics) -> pymupdf.Rect | None:
    x0, x1 = band

    def overlap(r: pymupdf.Rect) -> float:
        return min(r.x1, x1) - max(r.x0, x0)

    def inside(r: pymupdf.Rect) -> bool:  # mostly within the band
        return overlap(r) >= 0.6 * r.width

    # The figure sits between the caption and the nearest paragraph or other caption above it.
    walls = [b.rect for b in blocks if (b.paragraph or b.caption) and b.rect != cap]
    top = max((r.y1 for r in walls if r.y1 <= cap.y0 + 1 and overlap(r) > 0.5 * min(r.width, x1 - x0)),
              default=page.rect.height * 0.06)
    if cap.y0 - top < 40:
        return None
    parts = [g for g in graphics if g.y0 >= top - 2 and g.y1 <= cap.y0 + 2 and inside(g)]
    if not parts:
        return None  # nothing drawn above the caption
    region = pymupdf.Rect(parts[0])
    for g in parts[1:]:
        region |= g
    # Axis labels, legends and sub-captions touching the drawings belong to the figure; a title further up does not.
    labels = [b.rect for b in blocks
              if not (b.paragraph or b.caption) and b.rect.y0 >= top - 1 and b.rect.y1 <= cap.y0 + 1]
    for _ in range(3):
        near = region + (-8, -8, 8, 8)
        grown = pymupdf.Rect(region)
        for r in labels:
            if r.intersects(near) and inside(r):
                grown |= r
        if grown == region:
            break
        region = grown
    region = (region + (-4, -4, 4, 4)) & page.rect
    region.y1 = min(region.y1, cap.y0 - 1)
    if clamp:  # don't bleed into the neighbouring column or figure
        region &= pymupdf.Rect(x0, 0, x1, page.rect.height)
    if region.width < 60 or region.height < 40 or region.get_area() > 0.85 * page.rect.get_area():
        return None
    return region
