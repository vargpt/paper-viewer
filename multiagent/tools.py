"""Tools the experts call while they work, served in-process over MCP.

check_section  renders one section in a real browser (desktop + phone) and returns problems and screenshots
crop_figure    cuts a region out of a paper page into figures/, and shows the crop
"""
import base64
import re
from pathlib import Path

import pymupdf
from claude_agent_sdk import create_sdk_mcp_server, tool

from browser import DESKTOP
from page import assemble, static_check

SERVER = "studio"
MAX_IMAGES = 4


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
          "and screenshots of the section from top to bottom, with all <details> opened. Run it after every change "
          "and look at the screenshots.",
          {"section_id": str})
    async def check_section(args):
        sid = args.get("section_id", "")
        if sid not in section_ids:
            return _text(f"Unknown section '{sid}'. Sections: {', '.join(section_ids)}", error=True)
        path = ws / "sections" / f"{sid}.html"
        if not path.exists():
            return _text(f"sections/{sid}.html doesn't exist yet. Write it first.", error=True)
        problems = static_check(path.read_text(), sid, ws)
        report, images = [], []
        if browser.available:
            preview = ws / f"preview-{sid}.html"
            preview.write_text(assemble(ws, plan, readers=readers, rounds=rounds, only=sid))
            rendered = await browser.render(preview, shots=ws / "screenshots" / "checks", prefix=sid,
                                            selector=f"#{sid}", max_shots=MAX_IMAGES, open_details=True)
            if rendered:
                for where, found in rendered.problems.items():
                    problems += [p if where in (sid, "page") else f"[{where}] {p}" for p in found]
                images = [p.read_bytes() for p in rendered.shots]
                report.append(f"Section height: {rendered.height}px at {DESKTOP}px wide.")
                if rendered.height > MAX_IMAGES * 1500:
                    report.append(f"Only the first {MAX_IMAGES * 1500}px are shown. A section this long is hard to "
                                  "read: tighten it or move depth into <details>.")
        else:
            report.append(f"(No browser available: {browser.note}. Only static checks were run.)")
        head = f"Section '{sid}': " + (f"{len(problems)} problem(s):\n" + "\n".join(f"- {p}" for p in problems)
                                       if problems else "no problems found.")
        tail = " Screenshots follow, top to bottom; check that every visual looks right and is readable." if images else ""
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

    return {SERVER: create_sdk_mcp_server(SERVER, tools=[check_section, crop_figure])}
