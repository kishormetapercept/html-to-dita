import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from html import escape, unescape
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Union

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag
from fastapi import UploadFile


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}
HEADING_RE = re.compile(r"^h([1-6])$")
BLOCK_TAGS = {
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "ul",
    "ol",
    "table",
    "blockquote",
    "pre",
    "img",
}
SKIP_TAGS = {"script", "style", "noscript", "template", "meta", "link"}
MOJIBAKE_REPLACEMENTS = {
    "â€™": "'",
    "â€˜": "'",
    "â€œ": '"',
    "â€�": '"',
    "â€“": "-",
    "â€”": "-",
    "â€¦": "...",
    "â€": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2026": "...",
}


@dataclass
class SectionNode:
    title: str
    level: int
    blocks: List[str] = field(default_factory=list)
    children: List["SectionNode"] = field(default_factory=list)


def prepare_user_directories(user_id: str, input_root: str, output_root: str) -> tuple[str, str]:
    input_dir = Path(input_root) / user_id
    output_dir = Path(output_root) / user_id

    shutil.rmtree(input_dir, ignore_errors=True)
    shutil.rmtree(output_dir, ignore_errors=True)

    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    return str(input_dir), str(output_dir)


def remove_user_io_directories(user_id: str, input_root: str, output_root: str) -> None:
    shutil.rmtree(Path(input_root) / user_id, ignore_errors=True)
    shutil.rmtree(Path(output_root) / user_id, ignore_errors=True)


def save_and_extract_zip(upload: UploadFile, input_dir: str) -> str:
    file_name = upload.filename or "input.zip"
    if Path(file_name).suffix.lower() != ".zip":
        raise ValueError("Invalid file type. Please upload a .zip file.")

    target_path = Path(input_dir) / file_name
    with target_path.open("wb") as writer:
        shutil.copyfileobj(upload.file, writer)

    try:
        with zipfile.ZipFile(target_path, "r") as archive:
            archive.extractall(input_dir)
    except zipfile.BadZipFile as error:
        target_path.unlink(missing_ok=True)
        raise ValueError("Invalid zip file. Please upload a valid .zip archive.") from error

    target_path.unlink(missing_ok=True)
    return file_name


def find_html_files(base_dir: str) -> List[Path]:
    root = Path(base_dir)
    files: List[Path] = []
    files.extend(root.rglob("*.html"))
    files.extend(root.rglob("*.htm"))
    return files


def extract_title_from_html(file_path: Path) -> Optional[str]:
    content = file_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(content, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    return title or None


def find_html_without_title(html_files: Iterable[Path], base_dir: str) -> List[str]:
    invalid_files: List[str] = []
    base = Path(base_dir)
    for file_path in html_files:
        if not extract_title_from_html(file_path):
            invalid_files.append(str(file_path.relative_to(base)).replace("\\", "/"))
    return invalid_files


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return normalized or "topic"


def _normalize_text(value: str) -> str:
    text = value.replace("\xa0", " ")
    for source, target in MOJIBAKE_REPLACEMENTS.items():
        text = text.replace(source, target)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_text(node: Union[Tag, NavigableString, str]) -> str:
    if isinstance(node, NavigableString):
        return _normalize_text(str(node))
    if isinstance(node, str):
        return _normalize_text(node)
    return _normalize_text(node.get_text(" ", strip=True))


def _heading_level(tag: Tag) -> Optional[int]:
    match = HEADING_RE.match(tag.name.lower())
    if not match:
        return None
    return int(match.group(1))


def _extract_heading_text(heading_tag: Tag) -> str:
    inline_tag_names = {"a", "abbr", "b", "strong", "i", "em", "u", "sup", "sub", "span", "code", "small"}
    parts: List[str] = []

    for child in heading_tag.children:
        if isinstance(child, NavigableString):
            text = _extract_text(child)
            if text:
                parts.append(text)
            continue

        if not isinstance(child, Tag):
            continue

        if child.name.lower() in inline_tag_names:
            text = _extract_text(child)
            if text:
                parts.append(text)

    heading_text = _normalize_text(" ".join(parts))
    return heading_text or _extract_text(heading_tag)


def _repair_malformed_headings(root: Tag) -> None:
    movable_blocks = BLOCK_TAGS | {"div", "section", "article", "main", "header", "footer", "nav", "aside"}
    for heading in root.find_all(HEADING_RE):
        moved_children: List[Tag] = []
        for child in list(heading.children):
            if not isinstance(child, Tag):
                continue
            if child.name.lower() in movable_blocks:
                moved_children.append(child.extract())

        for moved in reversed(moved_children):
            heading.insert_after(moved)


def _iter_content_nodes(node: Tag) -> Iterator[Union[Tag, str]]:
    for child in node.children:
        if isinstance(child, NavigableString):
            text = _extract_text(child)
            if text:
                yield text
            continue

        if not isinstance(child, Tag):
            continue

        tag_name = child.name.lower()
        if tag_name in SKIP_TAGS:
            continue

        if tag_name in BLOCK_TAGS:
            yield child
            continue

        yielded_nested = False
        for nested in _iter_content_nodes(child):
            yielded_nested = True
            yield nested

        if not yielded_nested:
            text = _extract_text(child)
            if text:
                yield text


def _indent_fragment(fragment: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" if line else "" for line in fragment.splitlines())


def _convert_text_block(text: str) -> str:
    if not text:
        return "<p/>"
    return f"<p>{escape(text)}</p>"


def _convert_li(li: Tag) -> str:
    text_parts: List[str] = []
    nested_blocks: List[str] = []

    for child in li.children:
        if isinstance(child, NavigableString):
            text = _extract_text(child)
            if text:
                text_parts.append(text)
            continue

        if not isinstance(child, Tag):
            continue

        child_name = child.name.lower()
        if child_name in {"ul", "ol"}:
            nested_blocks.append(_convert_list(child))
            continue

        text = _extract_text(child)
        if text:
            text_parts.append(text)

    text = _normalize_text(" ".join(text_parts))

    if not nested_blocks:
        if text:
            return f"<li>{escape(text)}</li>"
        return "<li><p/></li>"

    lines = ["<li>"]
    if text:
        lines.append(f"  <p>{escape(text)}</p>")
    for nested in nested_blocks:
        lines.append(_indent_fragment(nested, 2))
    lines.append("</li>")
    return "\n".join(lines)


def _convert_list(list_tag: Tag) -> str:
    tag_name = "ol" if list_tag.name.lower() == "ol" else "ul"
    lines = [f"<{tag_name}>"]
    for li in list_tag.find_all("li", recursive=False):
        lines.append(_indent_fragment(_convert_li(li), 2))
    lines.append(f"</{tag_name}>")
    return "\n".join(lines)


def _normalize_table_row(cells: List[str], expected_cols: int) -> List[str]:
    if len(cells) >= expected_cols:
        return cells[:expected_cols]
    return cells + [""] * (expected_cols - len(cells))


def _extract_row_cells(row: Tag) -> List[str]:
    values: List[str] = []
    for cell in row.find_all(["th", "td"], recursive=False):
        text = _extract_text(cell)
        colspan_raw = str(cell.get("colspan", "1")).strip()
        colspan = int(colspan_raw) if colspan_raw.isdigit() else 1
        colspan = max(1, colspan)
        values.append(text)
        for _ in range(colspan - 1):
            values.append("")
    return values


def _convert_table(table_tag: Tag) -> str:
    header_rows_raw: List[List[str]] = []
    body_rows_raw: List[List[str]] = []

    thead = table_tag.find("thead")
    tbody = table_tag.find("tbody")

    if thead:
        for row in thead.find_all("tr", recursive=False):
            cells = _extract_row_cells(row)
            if cells:
                header_rows_raw.append(cells)

    if tbody:
        for row in tbody.find_all("tr", recursive=False):
            cells = _extract_row_cells(row)
            if cells:
                body_rows_raw.append(cells)
    else:
        for row in table_tag.find_all("tr", recursive=False):
            cells = _extract_row_cells(row)
            if cells:
                body_rows_raw.append(cells)

    if not header_rows_raw and body_rows_raw:
        first_tr = table_tag.find("tr")
        first_tr_cells = first_tr.find_all(["th", "td"], recursive=False) if first_tr else []
        if first_tr_cells and all(cell.name == "th" for cell in first_tr_cells):
            header_rows_raw = [body_rows_raw[0]]
            body_rows_raw = body_rows_raw[1:]

    all_rows = header_rows_raw + body_rows_raw
    if not all_rows:
        return "<p/>"

    cols = max(len(row) for row in all_rows)
    header_rows = [_normalize_table_row(row, cols) for row in header_rows_raw]
    body_rows = [_normalize_table_row(row, cols) for row in body_rows_raw]

    lines = ["<table>", f'  <tgroup cols="{cols}">']

    if header_rows:
        lines.append("    <thead>")
        for row in header_rows:
            lines.append("      <row>")
            for value in row:
                lines.append(f"        <entry>{escape(value)}</entry>")
            lines.append("      </row>")
        lines.append("    </thead>")

    lines.append("    <tbody>")
    for row in body_rows:
        lines.append("      <row>")
        for value in row:
            lines.append(f"        <entry>{escape(value)}</entry>")
        lines.append("      </row>")
    lines.append("    </tbody>")
    lines.append("  </tgroup>")
    lines.append("</table>")
    return "\n".join(lines)


def _convert_href(href: str) -> str:
    href = href.strip()
    if not href:
        return ""

    hash_part = ""
    if "#" in href:
        href, hash_part = href.split("#", 1)
        hash_part = f"#{hash_part}"

    if href.lower().endswith(".html"):
        href = f"{href[:-5]}.dita"
    elif href.lower().endswith(".htm"):
        href = f"{href[:-4]}.dita"

    return f"{href}{hash_part}"


def _resolve_image_href(src: str, dita_path: Path, output_root: Path) -> str:
    image_name = Path(src or "").name
    if not image_name:
        return ""
    target = output_root / "images" / image_name
    relative = os.path.relpath(target, start=dita_path.parent)
    return relative.replace("\\", "/")


def _convert_image(img_tag: Tag, dita_path: Path, output_root: Path) -> str:
    src = str(img_tag.get("src", "")).strip()
    href = _resolve_image_href(src, dita_path, output_root)
    if not href:
        return "<p/>"

    alt = _extract_text(str(img_tag.get("alt", "")).strip())
    lines = ["<fig>"]
    if alt:
        lines.append(f"  <title>{escape(alt)}</title>")
    lines.append(f'  <image href="{escape(href)}"/>')
    lines.append("</fig>")
    return "\n".join(lines)


def _convert_blockquote(blockquote: Tag) -> str:
    text = _extract_text(blockquote)
    if not text:
        return "<lq/>"
    return f"<lq>{escape(text)}</lq>"


def _convert_pre(pre_tag: Tag) -> str:
    text = _normalize_text(pre_tag.get_text("\n", strip=False))
    if not text:
        return "<codeblock/>"
    return f"<codeblock>{escape(text)}</codeblock>"


def _convert_content_block(node: Union[Tag, str], dita_path: Path, output_root: Path) -> str:
    if isinstance(node, str):
        return _convert_text_block(node)

    name = node.name.lower()
    if name == "p":
        return _convert_text_block(_extract_text(node))
    if name in {"ul", "ol"}:
        return _convert_list(node)
    if name == "table":
        return _convert_table(node)
    if name == "blockquote":
        return _convert_blockquote(node)
    if name == "img":
        return _convert_image(node, dita_path, output_root)
    if name == "pre":
        return _convert_pre(node)
    if name == "a":
        href = _convert_href(str(node.get("href", "")))
        label = _extract_text(node)
        if href and label and label != href:
            return f'<p><xref href="{escape(href)}">{escape(label)}</xref></p>'
        if href:
            return f'<p><xref href="{escape(href)}"/></p>'
        return _convert_text_block(label)

    return _convert_text_block(_extract_text(node))


def _shorten(text: str, limit: int = 220) -> str:
    if len(text) <= limit:
        return text
    trimmed = text[:limit]
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0]
    return f"{trimmed}..."


def _pick_shortdesc(nodes: List[Union[Tag, str]], title_index: Optional[int]) -> str:
    for idx, node in enumerate(nodes):
        if title_index is not None and idx <= title_index:
            continue

        if isinstance(node, Tag):
            if _heading_level(node) is not None:
                break
            if node.name.lower() not in {"p", "blockquote"}:
                continue

        text = _extract_text(node)
        if text:
            return _shorten(text)
    return ""


def _render_section(section: SectionNode, indent: int) -> List[str]:
    prefix = " " * indent
    lines = [f"{prefix}<section>", f"{prefix}  <title>{escape(section.title)}</title>"]

    for block in section.blocks:
        lines.append(_indent_fragment(block, indent + 2))

    for child in section.children:
        lines.extend(_render_section(child, indent + 2))

    lines.append(f"{prefix}</section>")
    return lines


def _render_concept_xml(topic_id: str, title: str, shortdesc: str, intro_blocks: List[str], root_sections: List[SectionNode]) -> str:
    conbody_lines = ["  <conbody>"]
    for block in intro_blocks:
        conbody_lines.append(_indent_fragment(block, 4))
    for section in root_sections:
        conbody_lines.extend(_render_section(section, 4))
    if len(conbody_lines) == 1:
        conbody_lines.append("    <p/>")
    conbody_lines.append("  </conbody>")

    lines = [
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
        "<!DOCTYPE concept PUBLIC \"-//OASIS//DTD DITA Concept//EN\" \"concept.dtd\">",
        f"<concept id=\"{escape(topic_id)}\">",
        f"  <title>{escape(title)}</title>",
    ]
    if shortdesc:
        lines.append(f"  <shortdesc>{escape(shortdesc)}</shortdesc>")
    lines.extend(conbody_lines)
    lines.append("</concept>")
    return "\n".join(lines) + "\n"


def _extract_text_from_block_xml(block_xml: str) -> str:
    raw = re.sub(r"<[^>]+>", " ", block_xml)
    return _normalize_text(unescape(raw))


def _pick_shortdesc_from_blocks(blocks: List[str]) -> str:
    for block in blocks:
        text = _extract_text_from_block_xml(block)
        if text:
            return _shorten(text)
    return ""


def _iter_sections_with_index(sections: List[SectionNode], prefix: tuple[int, ...] = ()):
    for index, section in enumerate(sections, start=1):
        current = prefix + (index,)
        yield section, current
        yield from _iter_sections_with_index(section.children, current)


def _assign_section_topic_files(root_sections: List[SectionNode]) -> dict[tuple[int, ...], str]:
    used_names: dict[str, int] = {}
    mapping: dict[tuple[int, ...], str] = {}

    for section, index_path in _iter_sections_with_index(root_sections):
        base = _slugify(section.title)
        count = used_names.get(base, 0) + 1
        used_names[base] = count
        suffix = "" if count == 1 else f"_{count:02d}"
        mapping[index_path] = f"{base}{suffix}.dita"

    return mapping


def _render_section_topicrefs(
    sections: List[SectionNode],
    section_file_map: dict[tuple[int, ...], str],
    prefix: tuple[int, ...] = (),
    indent: int = 6,
) -> List[str]:
    lines: List[str] = []
    for i, section in enumerate(sections, start=1):
        current = prefix + (i,)
        href = section_file_map[current]
        child_lines = _render_section_topicrefs(section.children, section_file_map, current, indent + 2)
        pad = " " * indent

        if child_lines:
            lines.append(f'{pad}<topicref href="{escape(href)}">')
            lines.extend(child_lines)
            lines.append(f"{pad}</topicref>")
        else:
            lines.append(f'{pad}<topicref href="{escape(href)}"/>')

    return lines


def _write_topic_folder_map(
    map_path: Path,
    map_title: str,
    main_topic_name: str,
    root_sections: List[SectionNode],
    section_file_map: dict[tuple[int, ...], str],
) -> None:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE map PUBLIC "-//OASIS//DTD DITA Map//EN" "map.dtd">',
        '<map>',
        f"  <title>{escape(map_title)}</title>",
    ]
    if root_sections:
        lines.append(f'  <topicref href="{escape(main_topic_name)}">')
        lines.extend(_render_section_topicrefs(root_sections, section_file_map))
        lines.append("  </topicref>")
    else:
        lines.append(f'  <topicref href="{escape(main_topic_name)}"/>')
    lines.append('</map>')

    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def convert_html_file_to_dita(html_path: Path, dita_path: Path, output_root: Path) -> List[str]:
    content = html_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(content, "html.parser")
    body = soup.body or soup
    _repair_malformed_headings(body)

    content_nodes = list(_iter_content_nodes(body))

    title = ""
    title_index: Optional[int] = None
    base_heading_level = 1
    for idx, node in enumerate(content_nodes):
        if not isinstance(node, Tag):
            continue
        level = _heading_level(node)
        if level is None:
            continue
        heading_text = _extract_heading_text(node)
        if not heading_text:
            continue

        title = heading_text
        title_index = idx
        base_heading_level = level
        break

    if not title:
        html_title = soup.title.get_text(strip=True) if soup.title else html_path.stem
        title = _normalize_text(html_title) or html_path.stem

    topic_id = _slugify(title)
    shortdesc = _pick_shortdesc(content_nodes, title_index)

    intro_blocks: List[str] = []
    root_sections: List[SectionNode] = []
    section_stack: List[SectionNode] = []

    for idx, node in enumerate(content_nodes):
        if idx == title_index:
            continue

        if isinstance(node, Tag):
            level = _heading_level(node)
            if level is not None:
                heading_text = _extract_heading_text(node)
                if not heading_text:
                    continue

                section = SectionNode(title=heading_text, level=max(level, base_heading_level + 1))
                while section_stack and section.level <= section_stack[-1].level:
                    section_stack.pop()
                if section_stack:
                    section_stack[-1].children.append(section)
                else:
                    root_sections.append(section)
                section_stack.append(section)
                continue

        block_xml = _convert_content_block(node, dita_path, output_root)
        if not block_xml:
            continue

        if section_stack:
            section_stack[-1].blocks.append(block_xml)
        else:
            intro_blocks.append(block_xml)

    main_relative = dita_path.relative_to(output_root)

    topic_folder_relative = main_relative.with_suffix("")
    topic_folder = output_root / topic_folder_relative
    topic_folder.mkdir(parents=True, exist_ok=True)

    main_topic_name = f"{_slugify(title)}.dita"
    main_topic_path = topic_folder / main_topic_name
    main_xml = _render_concept_xml(topic_id, title, shortdesc, intro_blocks, [])
    main_topic_path.write_text(main_xml, encoding="utf-8")

    section_file_map = _assign_section_topic_files(root_sections) if root_sections else {}

    for section, index_path in _iter_sections_with_index(root_sections):
        section_file = section_file_map[index_path]
        section_path = topic_folder / section_file
        section_topic_id = _slugify(section.title)
        section_shortdesc = _pick_shortdesc_from_blocks(section.blocks)
        section_xml = _render_concept_xml(
            section_topic_id,
            section.title,
            section_shortdesc,
            section.blocks,
            [],
        )
        section_path.write_text(section_xml, encoding="utf-8")

    map_name = f"{main_relative.stem}.ditamap"
    topic_map_path = topic_folder / map_name
    _write_topic_folder_map(topic_map_path, title, main_topic_name, root_sections, section_file_map)

    map_relative = topic_folder_relative / map_name
    return [map_relative.as_posix()]

def convert_input_to_dita(input_dir: str, output_dir: str) -> List[str]:
    input_root = Path(input_dir)
    output_root = Path(output_dir)
    dita_rel_paths: List[str] = []

    for source in input_root.rglob("*"):
        if source.is_dir():
            continue

        ext = source.suffix.lower()

        if ext in {".html", ".htm"}:
            relative = source.relative_to(input_root)
            dita_relative = relative.with_suffix(".dita")
            target = output_root / dita_relative
            generated = convert_html_file_to_dita(source, target, output_root)
            dita_rel_paths.extend(generated)
        elif ext in IMAGE_EXTENSIONS:
            target = output_root / "images" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    return dita_rel_paths


def write_ditamap(output_dir: str, dita_rel_paths: List[str]) -> None:
    topic_refs = "\n".join(
        f'  <topicref href="{escape(path)}" format="ditamap"/>' if path.lower().endswith(".ditamap")
        else f'  <topicref href="{escape(path)}"/>'
        for path in dita_rel_paths
    )
    map_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        '<!DOCTYPE map PUBLIC "-//OASIS//DTD DITA Map//EN" "map.dtd">\n'
        '<map>\n'
        '  <title>Index Ditamap</title>\n'
        f'{topic_refs}\n'
        '</map>\n'
    )
    (Path(output_dir) / "index.ditamap").write_text(map_xml, encoding="utf-8")

def create_zip_archive(source_dir: str, zip_path: str) -> None:
    source_root = Path(source_dir)
    target = Path(zip_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in source_root.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(source_root)
                zip_file.write(file_path, arcname)


def resolve_zip_file(download_dir: str, expected_name: Optional[str] = None) -> Optional[Path]:
    directory = Path(download_dir)
    if not directory.exists() or not directory.is_dir():
        return None

    if expected_name:
        expected = directory / expected_name
        if expected.exists() and expected.is_file():
            return expected

    candidates = sorted(directory.glob("*.zip"))
    return candidates[0] if candidates else None


def remove_directory(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def clear_directory_contents(path: str) -> None:
    directory = Path(path)
    if not directory.exists() or not directory.is_dir():
        return

    for child in directory.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
