"""
Markdown → HTML batch converter for price-action docs.
Uses the shared styles.css and a consistent template.
"""
import re
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
CSS_FILE = os.path.join(DIR, "styles.css")


def md_to_html(md_text: str, title: str) -> str:
    """Convert markdown to structured HTML with the shared template."""
    lines = md_text.split("\n")
    html_parts = []
    toc_items = []
    section_counter = 0

    i = 0
    while i < len(lines):
        line = lines[i]

        # --- Skip front matter (blockquote at top with source info) ---
        if i == 0 and line.startswith("# "):
            # Extract hero title
            h1_text = line[2:].strip()
            # Look for core thesis in first blockquote
            hero_title_html = convert_inline(h1_text)
            html_parts.append(("hero", hero_title_html, None))
            i += 1
            continue

        if line.startswith("> ") and i < 5 and not html_parts or (html_parts and html_parts[-1][0] == "hero"):
            # Collect blockquote lines at top (source info)
            bq_lines = []
            while i < len(lines) and lines[i].startswith("> "):
                bq_lines.append(lines[i][2:])
                i += 1
            bq_text = "\n".join(bq_lines)
            html_parts.append(("blockquote_top", bq_text, None))
            continue

        # --- Horizontal rule ---
        if re.match(r"^---+\s*$", line):
            html_parts.append(("hr", None, None))
            i += 1
            continue

        # --- H2 ---
        if line.startswith("## "):
            section_counter += 1
            heading_text = line[3:].strip()
            # Check if it uses Chinese numbering like 一、二、
            num_match = re.match(r"^([一二三四五六七八九十]+)、\s*(.*)", heading_text)
            if num_match:
                num_text = num_match.group(1)
                heading_text = num_match.group(2)
            else:
                num_text = str(section_counter)
            sid = f"s{section_counter}"
            toc_items.append((num_text, heading_text, sid))
            html_parts.append(("h2", heading_text, num_text, sid))
            i += 1
            continue

        # --- H3 ---
        if line.startswith("### "):
            heading_text = line[4:].strip()
            html_parts.append(("h3", heading_text))
            i += 1
            continue

        # --- H4 ---
        if line.startswith("#### "):
            heading_text = line[5:].strip()
            html_parts.append(("h4", heading_text))
            i += 1
            continue

        # --- Blockquote ---
        if line.startswith("> "):
            bq_lines = []
            while i < len(lines) and (lines[i].startswith("> ") or lines[i].strip() == ""):
                if lines[i].startswith("> "):
                    bq_lines.append(lines[i][2:])
                i += 1
            bq_text = "\n".join(bq_lines)
            html_parts.append(("blockquote", bq_text))
            continue

        # --- Table ---
        if "|" in line and i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1].strip()):
            table_lines = []
            while i < len(lines) and "|" in lines[i]:
                table_lines.append(lines[i])
                i += 1
            html_parts.append(("table", table_lines))
            continue

        # --- Code block ---
        if line.startswith("```"):
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing ```
            html_parts.append(("code", "\n".join(code_lines)))
            continue

        # --- Ordered list ---
        if re.match(r"^\d+\.\s", line):
            list_items = []
            while i < len(lines) and re.match(r"^\d+\.\s", lines[i]):
                item_text = re.sub(r"^\d+\.\s+", "", lines[i])
                list_items.append(item_text)
                i += 1
            html_parts.append(("ol", list_items))
            continue

        # --- Unordered list ---
        if line.startswith("- ") or line.startswith("* "):
            list_items = []
            while i < len(lines) and (lines[i].startswith("- ") or lines[i].startswith("* ")):
                item_text = lines[i][2:]
                list_items.append(item_text)
                i += 1
            html_parts.append(("ul", list_items))
            continue

        # --- Empty line ---
        if line.strip() == "":
            i += 1
            continue

        # --- Regular paragraph ---
        para_lines = []
        while i < len(lines) and lines[i].strip() != "" and not lines[i].startswith("#") and not lines[i].startswith(">") and not lines[i].startswith("```") and not lines[i].startswith("|") and not lines[i].startswith("- ") and not re.match(r"^\d+\.\s", lines[i]) and not re.match(r"^---+\s*$", lines[i]):
            para_lines.append(lines[i])
            i += 1
        if para_lines:
            html_parts.append(("p", " ".join(para_lines)))
        else:
            i += 1

    # --- Build HTML ---
    parts = []
    parts.append(f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(title)}</title>
<link rel="stylesheet" href="styles.css">
</head>
<body>
""")

    # Hero
    hero_title = title
    for item in html_parts:
        if item[0] == "hero":
            hero_title = item[1]
            break

    parts.append(f"""<div class="hero">
  <h1>{hero_title}</h1>
</div>

<div class="container">
""")

    # TOC
    if toc_items:
        parts.append('<div class="toc">\n  <h3>目录</h3>\n  <ol>')
        for num, text, sid in toc_items:
            parts.append(f'    <li><a href="#{sid}">{escape(text)}</a></li>')
        parts.append('  </ol>\n</div>\n')

    # Body — track section nesting to close divs
    section_depth = 0
    for item in html_parts:
        kind = item[0]

        if kind == "hero":
            pass  # already rendered

        elif kind == "blockquote_top":
            parts.append(f'<blockquote><p>{convert_inline(item[1])}</p></blockquote>\n')

        elif kind == "hr":
            parts.append('<hr>\n')

        elif kind == "h2":
            # Close previous section div
            if section_depth > 0:
                parts.append('</div>\n')
            section_depth += 1
            _, text, num, sid = item
            parts.append(f'<div class="section" id="{sid}">\n')
            parts.append(f'  <h2><span class="section-num">{escape(num)}</span> {convert_inline(text)}</h2>\n')

        elif kind == "h3":
            parts.append(f'  <h3>{convert_inline(item[1])}</h3>\n')

        elif kind == "h4":
            parts.append(f'  <h4>{convert_inline(item[1])}</h4>\n')

        elif kind == "p":
            parts.append(f'  <p>{convert_inline(item[1])}</p>\n')

        elif kind == "blockquote":
            parts.append(f'  <blockquote><p>{convert_inline(item[1])}</p></blockquote>\n')

        elif kind == "table":
            table_html = render_table(item[1])
            parts.append(f'  {table_html}\n')

        elif kind == "code":
            parts.append(f'  <pre>{escape(item[1])}</pre>\n')

        elif kind == "ol":
            parts.append('  <ol>\n')
            for li in item[1]:
                parts.append(f'    <li>{convert_inline(li)}</li>\n')
            parts.append('  </ol>\n')

        elif kind == "ul":
            parts.append('  <ul>\n')
            for li in item[1]:
                parts.append(f'    <li>{convert_inline(li)}</li>\n')
            parts.append('  </ul>\n')

    # Close last section div
    if section_depth > 0:
        parts.append('</div>\n')

    parts.append('</div>\n</body>\n</html>')
    return "\n".join(parts)


def convert_inline(text: str) -> str:
    """Convert inline markdown to HTML."""
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Code
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    # Line breaks in blockquotes
    text = text.replace("\n", "<br>\n")
    return text


def escape(text: str) -> str:
    """HTML-escape text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render_table(table_lines: list) -> str:
    """Render markdown table lines to HTML table."""
    if len(table_lines) < 2:
        return ""

    def parse_row(line):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        return cells

    header = parse_row(table_lines[0])
    # Skip separator line (table_lines[1])
    rows = [parse_row(line) for line in table_lines[2:] if line.strip()]

    html = "<table>\n<thead><tr>"
    for h in header:
        html += f"<th>{convert_inline(h)}</th>"
    html += "</tr></thead>\n<tbody>\n"

    for row in rows:
        html += "<tr>"
        for cell in row:
            html += f"<td>{convert_inline(cell)}</td>"
        html += "</tr>\n"

    html += "</tbody>\n</table>"
    return html


def main():
    md_files = [f for f in os.listdir(DIR) if f.endswith(".md")]

    for fname in sorted(md_files):
        md_path = os.path.join(DIR, fname)
        html_fname = fname.replace(".md", ".html")
        html_path = os.path.join(DIR, html_fname)

        # Force regenerate all
        if os.path.exists(html_path):
            os.remove(html_path)

        with open(md_path, "r", encoding="utf-8") as f:
            md_text = f.read()

        # Extract title from first H1
        title_match = re.search(r"^#\s+(.+)", md_text, re.MULTILINE)
        title = title_match.group(1) if title_match else fname.replace(".md", "")

        html = md_to_html(md_text, title)

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)

        print(f"  OK   {html_fname}")

    print("\nDone!")


if __name__ == "__main__":
    main()
