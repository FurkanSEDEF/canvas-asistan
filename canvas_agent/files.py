"""File processing: find attachments in an assignment, download them (read-only), extract text."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .canvas_client import CanvasClient

FILE_LINK_RE = re.compile(r"/(?:api/v1/)?(?:courses/\d+/)?files/(\d+)")
TEXT_EXT = {".txt", ".md", ".py", ".m", ".java", ".c", ".cpp", ".h", ".ino", ".csv", ".json", ".tex", ".html"}


def find_file_ids(assignment_raw: dict) -> list[str]:
    html = assignment_raw.get("description") or ""
    return sorted(set(FILE_LINK_RE.findall(html)))


def safe_name(name: str) -> str:
    return re.sub(r"[^\w.\- ]", "_", name)[:120] or "file"


def download_attachments(client: CanvasClient, assignment_raw: dict, dest: Path, max_mb: int) -> tuple[list[Path], list[str]]:
    """Returns (downloaded paths, human-readable problems). Never raises on permission errors."""
    dest.mkdir(parents=True, exist_ok=True)
    got, problems = [], []
    for fid in find_file_ids(assignment_raw):
        meta = client.get(f"/files/{fid}")
        if not meta.ok:
            problems.append(f"attachment {fid}: {meta.unavailable_reason}")
            continue
        res = client.download(meta.data["url"], max_mb * 1024 * 1024)
        if not res.ok:
            problems.append(f"attachment {meta.data.get('display_name', fid)}: {res.error or res.unavailable_reason}")
            continue
        path = dest / safe_name(meta.data.get("display_name", f"{fid}.bin"))
        path.write_bytes(res.data)
        got.append(path)
    return got, problems


def extract_text(path: Path) -> tuple[str, str]:
    """(text, note). Empty text + note explains why extraction was not possible."""
    ext = path.suffix.lower()
    try:
        if ext in TEXT_EXT:
            return path.read_text(encoding="utf-8", errors="replace"), ""
        if ext == ".docx":
            with zipfile.ZipFile(path) as z:
                root = ET.fromstring(z.read("word/document.xml"))
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            paras = ["".join(t.text or "" for t in p.iter(ns + "t")) for p in root.iter(ns + "p")]
            return "\n".join(paras), ""
        if ext == ".pptx":
            out = []
            with zipfile.ZipFile(path) as z:
                for n in sorted(z.namelist()):
                    if re.match(r"ppt/slides/slide\d+\.xml", n):
                        out.append(" ".join(t.text or "" for t in ET.fromstring(z.read(n)).iter(
                            "{http://schemas.openxmlformats.org/drawingml/2006/main}t")))
            return "\n".join(out), ""
        if ext == ".pdf":
            try:
                from pypdf import PdfReader  # optional dependency
            except ImportError:
                return "", "PDF text extraction needs `pip install pypdf`"
            text = "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)
            return text, "" if text.strip() else "PDF has no extractable text (scanned? needs OCR)"
        return "", f"unsupported file type {ext}"
    except Exception as e:
        return "", f"could not read {path.name}: {type(e).__name__}"
