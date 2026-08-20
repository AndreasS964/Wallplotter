"""Die Projektdokumentation als statische Website bauen.

Aufruf::

    python tools/build_site.py          # nach site/
    python tools/build_site.py --out /tmp/vorschau

Absichtlich ohne Jekyll und ohne Theme von der Stange: Die Dokumentation liegt
schon als Markdown im Repo, und alles, was hier passiert, ist sie zu rendern,
die Querverweise umzubiegen und ein Blatt CSS drumherum zu legen. Damit ist der
Bau lokal nachvollziehbar — ``python tools/build_site.py && python -m
http.server -d site`` zeigt genau das, was später online steht.

Gebraucht wird ``markdown``; ``pygments`` ist optional und macht die
Codeblöcke bunt.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BLOB = "https://github.com/AndreasS964/Wallplotter/blob/main"

TITLE = "Wallplotter"
TAGLINE = "Ein V-Plotter, der Wände bemalt — und die Software dahinter"


@dataclass(frozen=True)
class Page:
    """Eine Seite der Website."""

    source: Path
    target: str
    nav: str
    summary: str
    group: str


PAGES: list[Page] = [
    Page(REPO / "README.md", "index.html", "Übersicht", "Was das Projekt ist und was es kann", "Start"),
    Page(
        REPO / "docs/bauanleitung.md",
        "bauanleitung.html",
        "Bauanleitung",
        "Vom Karton bis zum ersten Strich",
        "Bauen",
    ),
    Page(
        REPO / "docs/projektidee.md",
        "projektidee.html",
        "Projektidee",
        "Hardware, Mechanik, verworfene Wege",
        "Bauen",
    ),
    Page(
        REPO / "docs/kinematik.md",
        "kinematik.html",
        "Kinematik",
        "Gerechnete Zahlen zur Aufhängung",
        "Bauen",
    ),
    Page(
        REPO / "docs/wandplotter-handbuch.md",
        "handbuch.html",
        "Projekthandbuch",
        "Alles an einer Stelle",
        "Nachschlagen",
    ),
    Page(
        REPO / "docs/firmware-gegenpruefung.md",
        "gegenpruefung.html",
        "Gegenprüfung",
        "Was der FluidNC-Quelltext sagt",
        "Nachschlagen",
    ),
    Page(
        REPO / "docs/software-roadmap.md",
        "roadmap.html",
        "Software-Roadmap",
        "Stufenplan und UI-Architektur",
        "Nachschlagen",
    ),
    Page(REPO / "CHANGELOG.md", "changelog.html", "Änderungen", "Was wann passiert ist", "Nachschlagen"),
]

#: Markdown-Ziel → Seite auf der Website. Alles, was hier nicht steht, zeigt
#: weiterhin auf GitHub — eine tote interne Verknüpfung wäre schlimmer als ein
#: Sprung ins Repo.
LINK_MAP = {
    "README.md": "index.html",
    "docs/bauanleitung.md": "bauanleitung.html",
    "bauanleitung.md": "bauanleitung.html",
    "docs/projektidee.md": "projektidee.html",
    "projektidee.md": "projektidee.html",
    "docs/kinematik.md": "kinematik.html",
    "kinematik.md": "kinematik.html",
    "docs/wandplotter-handbuch.md": "handbuch.html",
    "wandplotter-handbuch.md": "handbuch.html",
    "docs/firmware-gegenpruefung.md": "gegenpruefung.html",
    "firmware-gegenpruefung.md": "gegenpruefung.html",
    "docs/software-roadmap.md": "roadmap.html",
    "software-roadmap.md": "roadmap.html",
    "CHANGELOG.md": "changelog.html",
}

CSS = """
:root {
  color-scheme: light dark;
  --bg: #fbfaf8;
  --bg-soft: #f2efe9;
  --fg: #23201c;
  --fg-soft: #5d574e;
  --line: #e2ded5;
  --accent: #1a4fd6;
  --accent-soft: #e8eefc;
  --warn: #a8420a;
  --mono: ui-monospace, "SFMono-Regular", "Cascadia Code", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16151a;
    --bg-soft: #1e1d24;
    --fg: #e8e5e0;
    --fg-soft: #a49f97;
    --line: #302e38;
    --accent: #8aa9ff;
    --accent-soft: #21243a;
    --warn: #ff9d63;
  }
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  -webkit-text-size-adjust: 100%;
}

a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }
a:hover { text-decoration-thickness: 2px; }

.shell { display: flex; min-height: 100vh; align-items: flex-start; }

/* -- Seitenleiste -------------------------------------------------------- */

.sidebar {
  position: sticky;
  top: 0;
  flex: 0 0 16rem;
  height: 100vh;
  overflow-y: auto;
  padding: 1.8rem 1.2rem 3rem;
  background: var(--bg-soft);
  border-right: 1px solid var(--line);
}
.sidebar .brand { display: block; font-size: 1.25rem; font-weight: 700; color: var(--fg); text-decoration: none; }
.sidebar .tagline { margin: .35rem 0 1.6rem; font-size: .8rem; color: var(--fg-soft); line-height: 1.45; }
.sidebar h2 {
  margin: 1.5rem 0 .4rem;
  font-size: .7rem;
  letter-spacing: .09em;
  text-transform: uppercase;
  color: var(--fg-soft);
  font-weight: 600;
}
.sidebar nav a {
  display: block;
  padding: .32rem .6rem;
  margin-left: -.6rem;
  border-radius: 6px;
  color: var(--fg);
  text-decoration: none;
  font-size: .93rem;
}
.sidebar nav a:hover { background: var(--accent-soft); }
.sidebar nav a.active { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
.sidebar nav a small { display: block; color: var(--fg-soft); font-size: .74rem; font-weight: 400; line-height: 1.3; }
.sidebar .repo { margin-top: 2rem; font-size: .82rem; }

/* -- Inhalt -------------------------------------------------------------- */

main { flex: 1 1 auto; min-width: 0; padding: 2.4rem clamp(1rem, 4vw, 3.5rem) 6rem; }
/* Lange nackte URLs (die Projektidee ist voll davon) schoben sonst auf dem
   Handy die ganze Seite nach rechts. */
article { max-width: 46rem; margin: 0 auto; overflow-wrap: break-word; }
article a { overflow-wrap: anywhere; }

h1, h2, h3, h4 { line-height: 1.25; margin: 2.2rem 0 .7rem; font-weight: 650; }
h1 { font-size: 2.05rem; margin-top: 0; letter-spacing: -.02em; }
h2 { font-size: 1.4rem; padding-top: .9rem; border-top: 1px solid var(--line); }
h3 { font-size: 1.12rem; }
h4 { font-size: 1rem; color: var(--fg-soft); }
p, ul, ol { margin: .8rem 0; }
li { margin: .3rem 0; }
hr { border: 0; border-top: 1px solid var(--line); margin: 2.4rem 0; }
img { max-width: 100%; height: auto; border-radius: 8px; }
blockquote {
  margin: 1.2rem 0;
  padding: .1rem 1.1rem;
  border-left: 3px solid var(--warn);
  background: var(--bg-soft);
  border-radius: 0 6px 6px 0;
  color: var(--fg-soft);
}

code {
  font-family: var(--mono);
  font-size: .875em;
  background: var(--bg-soft);
  padding: .12em .38em;
  border-radius: 4px;
}
pre {
  background: var(--bg-soft);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: .9rem 1.1rem;
  overflow-x: auto;
  font-size: .86rem;
  line-height: 1.55;
}
pre code { background: none; padding: 0; font-size: inherit; }

/* Das README ist für GitHub geschrieben und benutzt zentrierte HTML-Blöcke
   samt Badges. Hier bekommen sie wenigstens vernünftige Abstände. */
p[align="center"] { text-align: center; }
p[align="center"] img { display: inline-block; margin: .15rem .2rem; vertical-align: middle; border-radius: 3px; }
p[align="center"] > img[width] { margin-top: 1rem; }
h1[align="center"] { text-align: center; }
table[align="center"], div[align="center"] { margin-inline: auto; }

.table-scroll { overflow-x: auto; margin: 1.2rem 0; }
table { border-collapse: collapse; width: 100%; font-size: .92rem; }
th, td { padding: .5rem .7rem; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-weight: 600; background: var(--bg-soft); white-space: nowrap; }
td code { white-space: nowrap; }

/* -- Kopf und Fuß -------------------------------------------------------- */

.page-head { margin-bottom: 2rem; }
.page-head .kicker { font-size: .72rem; letter-spacing: .09em; text-transform: uppercase; color: var(--fg-soft); }
.page-foot {
  max-width: 46rem;
  margin: 4rem auto 0;
  padding-top: 1.4rem;
  border-top: 1px solid var(--line);
  font-size: .84rem;
  color: var(--fg-soft);
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

/* -- Handy --------------------------------------------------------------- */

.nav-body > summary { display: none; }

@media (max-width: 860px) {
  .shell { display: block; }
  .sidebar {
    position: static;
    height: auto;
    width: 100%;
    border-right: 0;
    border-bottom: 1px solid var(--line);
    padding: 1rem 1.2rem;
  }
  .sidebar .tagline { margin: .3rem 0 0; }
  /* Eingeklappt: Sonst steht auf dem Handy erst ein Bildschirm voll
     Navigation, bevor der Text anfängt. Ohne JavaScript — <details> kann
     das seit Jahren. */
  .nav-body > summary {
    display: block;
    margin-top: .9rem;
    padding: .45rem .7rem;
    border: 1px solid var(--line);
    border-radius: 7px;
    font-size: .9rem;
    font-weight: 600;
    cursor: pointer;
    list-style: none;
  }
  .nav-body > summary::after { content: " ▾"; color: var(--fg-soft); }
  .nav-body[open] > summary::after { content: " ▴"; }
  .nav-body > summary::-webkit-details-marker { display: none; }
  .sidebar nav { display: grid; grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); gap: .1rem; }
  .sidebar nav a small { display: none; }
  .sidebar h2 { margin-top: .9rem; }
  .sidebar .repo { margin-top: 1rem; }
  main { padding: 1.6rem 1.1rem 4rem; }
  h1 { font-size: 1.6rem; }
}
"""


def render_markdown(text: str):
    """Markdown → HTML. Ohne ``markdown`` gibt es eine klare Fehlermeldung."""
    try:
        import markdown  # noqa: PLC0415
    except ImportError:  # pragma: no cover - nur beim Bauen relevant
        raise SystemExit(
            "Das Paket `markdown` fehlt:  pip install markdown pygments"
        ) from None

    extensions = ["extra", "sane_lists", "toc", "admonition"]
    configs: dict[str, dict] = {"toc": {"permalink": False}}
    try:
        import pygments  # noqa: F401,PLC0415

        extensions.append("codehilite")
        configs["codehilite"] = {"guess_lang": False, "noclasses": True}
    except ImportError:  # pragma: no cover
        pass
    return markdown.Markdown(extensions=extensions, extension_configs=configs)


def rewrite_links(body: str) -> str:
    """Interne Markdown-Verweise auf die gebauten Seiten umbiegen.

    Alles, was nicht zur Website gehört (``config/…``, ``LICENSE``, Quelltext),
    zeigt auf GitHub. Eine tote interne Verknüpfung wäre schlimmer als ein
    Sprung ins Repo.
    """

    def replace(match: re.Match) -> str:
        target = match.group("target")
        if target.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        path, _, anchor = target.partition("#")
        clean = path.lstrip("./")
        if clean.startswith("../"):
            clean = clean[3:]
        mapped = LINK_MAP.get(clean)
        if mapped:
            new = mapped + (f"#{anchor}" if anchor else "")
        elif clean.startswith("docs/images/") or clean.startswith("images/"):
            new = "images/" + clean.split("images/", 1)[1]
        elif not clean:
            new = target
        else:
            new = f"{BLOB}/{clean}" + (f"#{anchor}" if anchor else "")
        return match.group(0).replace(target, new, 1)

    body = re.sub(r'href="(?P<target>[^"]+)"', replace, body)
    return re.sub(r'src="(?P<target>[^"]+)"', replace, body)


def wrap_tables(body: str) -> str:
    """Breite Tabellen dürfen scrollen, nicht die Seite."""
    return body.replace("<table>", '<div class="table-scroll"><table>').replace(
        "</table>", "</table></div>"
    )


def navigation(active: str) -> str:
    parts = []
    for group in dict.fromkeys(page.group for page in PAGES):
        parts.append(f"<h2>{html.escape(group)}</h2><nav>")
        for page in PAGES:
            if page.group != group:
                continue
            css = ' class="active"' if page.target == active else ""
            parts.append(
                f'<a href="{page.target}"{css}>{html.escape(page.nav)}'
                f"<small>{html.escape(page.summary)}</small></a>"
            )
        parts.append("</nav>")
    return "".join(parts)


def page_html(page: Page, body: str, title: str) -> str:
    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · {TITLE}</title>
<meta name="description" content="{html.escape(page.summary)}">
<meta name="color-scheme" content="light dark">
<link rel="stylesheet" href="style.css">
</head>
<body>
<div class="shell">
<aside class="sidebar">
  <a class="brand" href="index.html">{TITLE}</a>
  <p class="tagline">{html.escape(TAGLINE)}</p>
  <details class="nav-body" open>
    <summary>Inhalt</summary>
    {navigation(page.target)}
    <p class="repo"><a href="https://github.com/AndreasS964/Wallplotter">Quelltext auf GitHub →</a></p>
  </details>
</aside>
<main>
<article>
<div class="page-head"><span class="kicker">{html.escape(page.group)}</span></div>
{body}
</article>
<footer class="page-foot">
  <span>{TITLE} · MIT-Lizenz</span>
  <span><a href="{BLOB}/{page.source.relative_to(REPO).as_posix()}">Diese Seite als Markdown</a></span>
</footer>
</main>
</div>
</body>
</html>
"""


def build(out: Path) -> int:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    images = out / "images"
    images.mkdir()
    for image in sorted((REPO / "docs/images").iterdir()):
        if image.is_file():
            shutil.copy2(image, images / image.name)

    (out / "style.css").write_text(CSS.strip() + "\n", encoding="utf-8")
    # GitHub Pages schiebt sonst alles durch Jekyll und schluckt Ordner mit
    # führendem Unterstrich.
    (out / ".nojekyll").write_text("", encoding="utf-8")

    converter = render_markdown("")
    for page in PAGES:
        text = page.source.read_text(encoding="utf-8")
        converter.reset()
        body = converter.convert(text)
        body = wrap_tables(rewrite_links(body))
        heading = re.search(r"<h1[^>]*>(.*?)</h1>", body, flags=re.S)
        title = re.sub(r"<[^>]+>", "", heading.group(1)).strip() if heading else page.nav
        (out / page.target).write_text(page_html(page, body, title), encoding="utf-8")
        print(f"  {page.source.relative_to(REPO)} → {page.target}")

    print(f"\n{len(PAGES)} Seiten in {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=REPO / "site", help="Zielverzeichnis")
    args = parser.parse_args(argv)
    return build(args.out)


if __name__ == "__main__":
    sys.exit(main())
