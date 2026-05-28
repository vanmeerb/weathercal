from pathlib import Path

import markdown


ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DIR = ROOT / "public"
SOURCE_MD = PUBLIC_DIR / "index.md"
TARGET_HTML = PUBLIC_DIR / "index.html"


def main() -> None:
    markdown_text = SOURCE_MD.read_text(encoding="utf-8")
    html_body = markdown.markdown(markdown_text)

    html = f"""<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
    <title>Leuven Weather Calendar</title>
    <style>
      :root {{
        --bg-start: #0b1f3a;
        --bg-end: #123d66;
        --card: rgba(255, 255, 255, 0.12);
        --text: #f4f7fb;
        --muted: #d2deec;
        --accent: #ffcc66;
        --accent-2: #ffd98f;
      }}

      * {{ box-sizing: border-box; }}

      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        font-family: \"Avenir Next\", \"Segoe UI\", sans-serif;
        color: var(--text);
        background: radial-gradient(circle at 20% 20%, #25588a 0%, transparent 35%),
          linear-gradient(145deg, var(--bg-start), var(--bg-end));
      }}

      main {{
        width: min(720px, calc(100vw - 2rem));
        padding: 2rem;
        border-radius: 18px;
        background: var(--card);
        backdrop-filter: blur(6px);
        border: 1px solid rgba(255, 255, 255, 0.2);
        box-shadow: 0 18px 40px rgba(0, 0, 0, 0.25);
      }}

      h1 {{ margin-top: 0; font-size: clamp(1.8rem, 5vw, 2.4rem); }}
      p, li {{ color: var(--muted); line-height: 1.5; }}
      a {{ color: var(--accent); font-weight: 700; }}
      a:hover {{ color: var(--accent-2); }}
      code {{
        background: rgba(0, 0, 0, 0.35);
        color: #f7f9fc;
        padding: 0.15rem 0.35rem;
        border-radius: 6px;
      }}
    </style>
  </head>
  <body>
    <main>
      {html_body}
    </main>
  </body>
</html>
"""

    TARGET_HTML.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
