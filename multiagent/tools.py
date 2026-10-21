"""Tools the experts call while they work, served in-process over MCP.

check_section   renders one section in a real browser (desktop + phone), operates its widgets, and returns problems
                and screenshots
crop_figure     cuts a region out of a paper page into figures/, and shows the crop
extract_images  saves the photos embedded in a paper page at full resolution into figures/, for widgets
"""
import base64
import re
from pathlib import Path

import pymupdf
from claude_agent_sdk import create_sdk_mcp_server, tool

from browser import DESKTOP
from page import assemble, concept_check, main_words, static_check
from prompts import SECTION_WORDS

SERVER = "studio"
MAX_IMAGES = 4
MAX_PLAYS = 3  # widgets operated per check


def _text(message: str, images: list[bytes] = (), mime: str = "image/jpeg", error: bool = False) -> dict:
    content = [{"type": "text", "text": message}]
    content += [{"type": "image", "data": base64.b64encode(b).decode(), "mimeType": mime} for b in images]
    return {"content": content, "is_error": error}


def expert_tools(ws: Path, plan: dict, paper, browser, *, readers: list[str], rounds: int) -> dict:
    """The MCP server config for one expert session."""
    section_ids = [s["id"] for s in plan["sections"]]

    @tool("check_section",
          "Render one section of the page in a real browser, at desktop width and at phone width, exactly as it will "
          "appear on the page (shared styles, Plotly, KaTeX and PV are loaded). Returns every problem found (HTML "
          "mistakes, JavaScript errors, math that failed to render, broken images, empty charts, overflow on phones) "
          "and screenshots of the section from top to bottom, with all <details> opened. It also operates each "
          "widget once (moves its first slider to the far end, or clicks a button) and shows the widget afterwards, "
          "and reports widgets that don't visibly change and planned concepts that have no visual. Run it when you "
          "have finished a batch of changes to a section (not after each edit) and look at the screenshots.",
          {"section_id": str})
    async def check_section(args):
        sid = args.get("section_id", "")
        if sid not in section_ids:
            return _text(f"Unknown section '{sid}'. Sections: {', '.join(section_ids)}", error=True)
        path = ws / "sections" / f"{sid}.html"
        if not path.exists():
            return _text(f"sections/{sid}.html doesn't exist yet. Write it first.", error=True)
        section = next(s for s in plan["sections"] if s["id"] == sid)
        problems = static_check(path.read_text(), sid, ws) + concept_check(path.read_text(), section)
        words = main_words(path.read_text())
        over = words - SECTION_WORDS
        report, images = [f"Main text: {words} words outside details blocks (budget {SECTION_WORDS})."
                          + (f" Over budget by {over}: cut them in one pass. Decide what goes (repetition, background, "
                             "detail the point doesn't need, or a paragraph that belongs in a details block) and rewrite "
                             "those parts in one go; don't trim a few words per edit." if over > 0 else "")], []
        if browser.available:
            preview = ws / f"preview-{sid}.html"
            preview.write_text(assemble(ws, plan, readers=readers, rounds=rounds, only=sid))
            rendered = await browser.render(preview, shots=ws / "screenshots" / "checks", prefix=sid,
                                            selector=f"#{sid}", max_shots=MAX_IMAGES, open_details=True, max_plays=MAX_PLAYS)
            if rendered:
                for where, found in rendered.problems.items():
                    problems += [p if where in (sid, "page") else f"[{where}] {p}" for p in found]
                images = [p.read_bytes() for p in rendered.shots] + [p.read_bytes() for p, _ in rendered.plays]
                report.append(f"Section height: {rendered.height}px at {DESKTOP}px wide.")
                if rendered.plays:
                    report.append("After the section's screenshots come the widgets after being operated, in order: "
                                  + "; ".join(what for _, what in rendered.plays) + ".")
                if rendered.height > MAX_IMAGES * 1500:
                    report.append(f"Only the first {MAX_IMAGES * 1500}px are shown. A section this long is hard to "
                                  "read: tighten it or move depth into <details>.")
        else:
            report.append(f"(No browser available: {browser.note}. Only static checks were run.)")
        head = f"Section '{sid}': " + (f"{len(problems)} problem(s):\n" + "\n".join(f"- {p}" for p in problems)
                                       if problems else "no problems found.")
        tail = (" Screenshots follow, top to bottom; check that every visual looks right and is readable, and that "
                "operating a widget changes it in a way that teaches something." if images else "")
        return _text("\n".join([head, *report]) + tail, images)

    @tool("crop_figure",
          "Cut a rectangle out of a page of the paper and save it as figures/<name>.png for use in your section "
          "(<img src=\"figures/<name>.png\">). Coordinates are fractions of the page: left/top 0 is the top-left "
          "corner, right/bottom 1 the bottom-right. Look at pages/page-NN.png first. Returns the crop so you can "
          "check it; crop again if it cuts something off or includes surrounding text.",
          {"type": "object",
           "properties": {
               "page": {"type": "integer", "description": "page number, starting at 1"},
               "left": {"type": "number"}, "top": {"type": "number"},
               "right": {"type": "number"}, "bottom": {"type": "number"},
               "name": {"type": "string", "description": "file name without extension: lowercase letters, digits, hyphens"},
           },
           "required": ["page", "left", "top", "right", "bottom", "name"]})
    async def crop_figure(args):
        if paper.kind != "pdf":
            return _text("This paper isn't a PDF, so there are no pages to crop.", error=True)
        doc = pymupdf.open(paper.source)
        n = int(args["page"])
        if not 1 <= n <= doc.page_count:
            return _text(f"Page must be between 1 and {doc.page_count}.", error=True)
        name = re.sub(r"[^a-z0-9-]+", "-", str(args["name"]).lower()).strip("-")[:40] or "crop"
        if re.fullmatch(r"fig\d+", name):
            name += "-crop"  # keep the automatic crops
        page = doc[n - 1]
        r = page.rect
        clip = pymupdf.Rect(r.x0 + float(args["left"]) * r.width, r.y0 + float(args["top"]) * r.height,
                            r.x0 + float(args["right"]) * r.width, r.y0 + float(args["bottom"]) * r.height) & r
        if clip.is_empty or clip.width < 20 or clip.height < 20:
            return _text("That rectangle is empty or tiny; check the coordinates (fractions between 0 and 1).", error=True)
        zoom = min(2.5, 1800 / clip.width)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
        out = ws / "figures" / f"{name}.png"
        out.parent.mkdir(exist_ok=True)
        pix.save(out)
        return _text(f"Saved figures/{name}.png ({pix.width}x{pix.height}px). Here it is:", [out.read_bytes()], mime="image/png")

    @tool("extract_images",
          "List the photos and raster images embedded in one page of the paper and save each at full resolution "
          "(up to 1200px wide) as figures/p<page>-<n>.jpg. Unlike a crop, you get the original pixels of each panel "
          "(e.g. the input, output and intermediate images of a figure, separately), which is what PV.imageLab "
          "needs to build a widget on the paper's real images. Returns the list and a preview of each image.",
          {"page": int})
    async def extract_images(args):
        if paper.kind != "pdf":
            return _text("This paper isn't a PDF, so there are no embedded images.", error=True)
        doc = pymupdf.open(paper.source)
        n = int(args["page"])
        if not 1 <= n <= doc.page_count:
            return _text(f"Page must be between 1 and {doc.page_count}.", error=True)
        page, seen, lines, previews = doc[n - 1], set(), [], []
        r = page.rect
        for info in page.get_image_info(xrefs=True):
            xref, (x0, y0, x1, y1) = info["xref"], info["bbox"]
            if not xref or xref in seen or min(info["width"], info["height"]) < 120:
                continue
            seen.add(xref)
            try:
                pix = pymupdf.Pixmap(doc, xref)
                if pix.alpha:
                    pix = pymupdf.Pixmap(pix, 0)
                if pix.n - pix.alpha > 3 or pix.colorspace is None or pix.colorspace.n != 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                while pix.width > 1200:
                    pix.shrink(1)
            except Exception:
                continue
            name = f"p{n}-{len(lines) + 1}"
            out = ws / "figures" / f"{name}.jpg"
            out.parent.mkdir(exist_ok=True)
            out.write_bytes(pix.tobytes("jpg", jpg_quality=88))
            lines.append(f"- figures/{name}.jpg: {pix.width}x{pix.height}px, at left {x0 / r.width:.2f}, top {y0 / r.height:.2f}, "
                         f"right {x1 / r.width:.2f}, bottom {y1 / r.height:.2f} of the page")
            if len(previews) < 8:
                small = pymupdf.Pixmap(pix, 0) if pix.alpha else pix
                while small.width > 360:
                    small.shrink(1)
                previews.append(small.tobytes("jpg", jpg_quality=70))
        if not lines:
            return _text(f"Page {n} has no embedded raster images (its figures are vector drawings); use crop_figure.")
        return _text(f"Saved {len(lines)} image(s) from page {n}:\n" + "\n".join(lines)
                     + (f"\nPreviews of the first {len(previews)}, in order:" if previews else ""), previews)

    return {SERVER: create_sdk_mcp_server(SERVER, tools=[check_section, crop_figure, extract_images])}
