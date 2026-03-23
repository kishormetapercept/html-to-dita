import re
import shutil
import zipfile
from html import escape
from pathlib import Path
from typing import Iterable, List, Optional

from bs4 import BeautifulSoup
from fastapi import UploadFile


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}


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
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return normalized or "topic"


def _collect_paragraphs(soup: BeautifulSoup) -> List[str]:
    body = soup.body or soup
    blocks = body.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6"])
    paragraphs = [block.get_text(" ", strip=True) for block in blocks if block.get_text(strip=True)]
    if paragraphs:
        return paragraphs

    fallback = body.get_text(" ", strip=True)
    return [fallback] if fallback else []


def convert_html_file_to_dita(html_path: Path, dita_path: Path) -> None:
    content = html_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(content, "html.parser")

    title = soup.title.get_text(strip=True) if soup.title else html_path.stem
    topic_id = _slugify(html_path.stem)
    paragraphs = _collect_paragraphs(soup)
    paragraph_xml = "\n".join(f"    <p>{escape(paragraph)}</p>" for paragraph in paragraphs)
    if not paragraph_xml:
        paragraph_xml = "    <p/>"

    dita = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<!DOCTYPE topic PUBLIC \"-//OASIS//DTD DITA Topic//EN\" \"topic.dtd\">\n"
        f"<topic id=\"{escape(topic_id)}\">\n"
        f"  <title>{escape(title)}</title>\n"
        "  <body>\n"
        f"{paragraph_xml}\n"
        "  </body>\n"
        "</topic>\n"
    )

    dita_path.parent.mkdir(parents=True, exist_ok=True)
    dita_path.write_text(dita, encoding="utf-8")


def convert_input_to_dita(input_dir: str, output_dir: str) -> List[str]:
    input_root = Path(input_dir)
    output_root = Path(output_dir)
    dita_rel_paths: List[str] = []

    for source in input_root.rglob("*"):
        if source.is_dir():
            continue

        relative = source.relative_to(input_root)
        ext = source.suffix.lower()

        if ext in {".html", ".htm"}:
            dita_relative = relative.with_suffix(".dita")
            target = output_root / dita_relative
            convert_html_file_to_dita(source, target)
            dita_rel_paths.append(str(dita_relative).replace("\\", "/"))
        elif ext in IMAGE_EXTENSIONS:
            target = output_root / "images" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    return dita_rel_paths


def write_ditamap(output_dir: str, dita_rel_paths: List[str]) -> None:
    topic_refs = "\n".join(f'  <topicref href="{escape(path)}"/>' for path in dita_rel_paths)
    map_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"no\"?>\n"
        "<!DOCTYPE map PUBLIC \"-//OASIS//DTD DITA Map//EN\" \"map.dtd\">\n"
        "<map>\n"
        "  <title>Index Ditamap</title>\n"
        f"{topic_refs}\n"
        "</map>\n"
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
