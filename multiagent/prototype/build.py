"""Build the visual-library demo page: prototype/demo.html (images inlined, like a final page).

python multiagent/prototype/build.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from page import CSS, KATEX, PLOTLY, PV_JS, SHELL, SHELL_JS, VISUAL_CSS, VISUAL_JS, fill, inline_images, tag_scripts  # noqa: E402

SECTIONS = [("wipe", "What it does"), ("pipeline", "The camera pipeline"), ("mixing", "Mixing as light")]


def main() -> None:
    parts = [tag_scripts((HERE / "sections" / f"{sid}.html").read_text(), sid) for sid, _ in SECTIONS]
    page = fill(SHELL, title="Visual library prototype", description="Prototype widgets for Removing Reflections from RAW Photos",
                katex=KATEX, plotly=PLOTLY, css=CSS + VISUAL_CSS, pv_js=PV_JS + VISUAL_JS, shell_js=SHELL_JS,
                hero='<header class="hero"><div class="hero-inner"><h1>Removing Reflections from RAW Photos</h1>'
                     '<p class="subtitle">Prototype of the visual library: three widgets on the paper\'s Figure 1 photos.</p></div></header>',
                toc="".join(f'<a href="#{sid}">{title}</a>' for sid, title in SECTIONS),
                sections="\n".join(parts), glossary="", footer="")
    out = HERE / "demo.html"
    out.write_text(inline_images(page, HERE))
    print(f"wrote {out} ({out.stat().st_size // 1000} KB)")


if __name__ == "__main__":
    main()
