"""
Page filter and work units.

Each page is labelled from its text layer (no GPU needed):
    procedure   experimental text with amounts      -> sent as TEXT (fast, exact numbers)
    scanned     no text layer, page is an image      -> sent as an IMAGE
    spectrum    NMR / IR / MS spectra                -> skipped
    references  reference list                       -> skipped
    other       introduction, discussion, blank...   -> skipped
A page whose text mentions an optimization / screening table is also sent as an IMAGE with the
table prompt, because tables lose their layout in the text layer.

Procedure text from consecutive pages is joined and cut into chunks at paragraph boundaries,
preferring cuts in front of a new procedure, so a procedure split over two pages stays together.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from modules import pdf_extraction as px

PROCEDURE_PATTERNS = [
    r"\bmmol\b", r"\bm[lL]\b|\bµ[lL]\b|\bu[lL]\b", r"\bequiv\b|\beq\.", r"mol\s?%",
    r"\byield", r"general\s+procedure|typical\s+procedure|\bGP\s?[A-Z0-9]\b",
    r"\b(was|were)\s+(added|stirred|heated|cooled|charged|dissolved)", r"\bstirr",
    r"°\s?C|\bdegC\b|\boC\b", r"purified|chromatograph", r"\b\d+(\.\d+)?\s?mg\b",
    r"\b(extracted|concentrated|filtered|evaporated)\b",
    r"\b(white|yellow|colou?rless|brown|orange|pale)\s+(solid|oil|powder|crystals?)\b",
]
_SPECTRUM_RE = re.compile(r"\bppm\b|\b(1H|13C|19F|31P)\s*(NMR|\{)|\bcm-1\b|\bm/z\b|chemical shift", re.I)
_REFERENCE_RE = re.compile(   # case-sensitive: journal abbreviations, DOIs, "2019, 141, 1234"
    r"\b(J\.\s?Am\.\s?Chem|Angew\.|Org\.\s?Lett|J\.\s?Org\.\s?Chem|Chem\.\s?Rev|Chem\.\s?Sci|Nat\.\s?Chem|"
    r"Chem\.\s?Commun|Tetrahedron(\s?Lett)?\.|Synthesis\s\d{4}|Green\s?Chem\.|ACS\s?Catal\.)|\bdoi:|"
    r"\b(19|20)\d{2},\s*\d{1,4},\s*\d{1,6}\b|\bet al\.")
_TABLE_RE = re.compile(r"\bTable\s*S?\d+", re.I)
_OPTIM_RE = re.compile(r"optimi[sz]|screening|\bentry\b|reaction\s+conditions|condition\s+survey", re.I)
_START_RE = re.compile(
    r"^\s*(general\s+(procedure|method)|typical\s+procedure|representative\s+procedure|\bGP\s?[A-Z0-9]\b|"
    r"synthesis\s+of|preparation\s+of|procedure\s+for|method\s+[A-Z0-9]\b|compound\s+\d|"
    r"\(?\d{1,3}[a-z]{0,2}\)?\s*[:.]\s|[^\n]{3,150}\(\s*\d{1,3}[a-z]{0,2}\s*\)\s*[:.]?\s*$)",
    re.I | re.M)
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>,;]+", re.I)


@dataclass
class PageInfo:
    page: int                 # 1-based
    label: str
    score: int = 0
    n_chars: int = 0
    has_table: bool = False
    image_coverage: float = 0.0


@dataclass
class Unit:
    unit_id: str
    kind: str                 # "procedure" | "table"
    mode: str                 # "text" | "image"
    pages: list[int]          # 1-based
    text: str = ""
    png: bytes = field(default=b"", repr=False)


def paper_id_for(pdf_bytes: bytes) -> str:
    return hashlib.sha1(pdf_bytes).hexdigest()[:12]


def procedure_score(text: str) -> int:
    return sum(1 for p in PROCEDURE_PATTERNS if re.search(p, text, re.I))


def classify_page(text: str, image_coverage: float, cfg: dict) -> PageInfo:
    f = cfg["page_filter"]
    t = text or ""
    n_chars = len(t.strip())
    words = re.findall(r"[A-Za-z]{3,}", t)
    score = procedure_score(t)
    has_table = bool(_TABLE_RE.search(t) and _OPTIM_RE.search(t))
    info = PageInfo(page=0, label="other", score=score, n_chars=n_chars, has_table=has_table,
                    image_coverage=image_coverage)
    # characterization pages ("3a: white solid (85 mg, 90%); 1H NMR ...") score as procedures and are kept
    if _SPECTRUM_RE.search(t) and len(words) < f["spectrum_max_words"] and score < f["min_procedure_score"]:
        info.label = "spectrum"
    elif len(_REFERENCE_RE.findall(t)) >= f["reference_min_hits"] and score < f["min_procedure_score"]:
        info.label = "references"
    elif score >= f["min_procedure_score"]:
        info.label = "procedure"
    elif n_chars < f["min_text_chars"]:
        info.label = "scanned" if image_coverage >= 0.5 else ("spectrum" if _SPECTRUM_RE.search(t) else "other")
    return info


def _image_coverage(page) -> float:
    area = abs(page.rect) or 1.0
    try:
        rects = [i["bbox"] for i in page.get_image_info()]
    except Exception:
        return 0.0
    covered = sum(abs(px._pymupdf().Rect(r) & page.rect) for r in rects)
    return min(1.0, covered / area)


def render(page, max_side_px: int) -> bytes:
    longest_in = max(page.rect.width, page.rect.height) / 72 or 1
    dpi = max(50, min(200, int(max_side_px / longest_in)))
    return page.get_pixmap(dpi=dpi).tobytes("png")


def _blocks(page) -> list[str]:
    out = []
    for b in page.get_text("blocks"):
        if len(b) >= 7 and b[6] != 0:          # image block
            continue
        t = re.sub(r"[ \t]+", " ", str(b[4])).strip()
        if t:
            out.append(t)
    return out


def chunk_blocks(blocks: list[tuple[int, str]], max_chars: int) -> list[list[tuple[int, str]]]:
    """Group (page, block) pairs into chunks of at most ~max_chars, never splitting a block, and
    cutting in front of a block that starts a new procedure whenever one is available."""
    chunks, cur, size = [], [], 0
    for pb in blocks:
        n = len(pb[1]) + 2
        if cur and size + n > max_chars:
            cut = next((i for i in range(len(cur) - 1, 0, -1)
                        if i >= len(cur) // 3 and _START_RE.search(cur[i][1][:200])), None)
            if cut:
                chunks.append(cur[:cut])
                cur = cur[cut:]
            else:
                chunks.append(cur)
                cur = []
            size = sum(len(b[1]) + 2 for b in cur)
        cur.append(pb)
        size += n
    if cur:
        chunks.append(cur)
    return chunks


def find_doi(text: str) -> Optional[str]:
    m = _DOI_RE.search(text or "")
    return m.group(0).rstrip(".)]") if m else None


def plan_paper(pdf_bytes: bytes, cfg: dict, page_indices: Optional[Iterable[int]] = None,
               paper_id: Optional[str] = None) -> tuple[list[PageInfo], list[Unit], dict]:
    """Label every page and build the work units. page_indices are 0-based (default: all pages)."""
    pid = paper_id or paper_id_for(pdf_bytes)
    max_chars = cfg["chunking"]["max_chars"]
    max_side = cfg["images"]["max_side_px"]
    infos, units = [], []
    runs: list[list[tuple[int, str]]] = []
    doi = None
    with px._open(pdf_bytes) as doc:
        n = len(doc)
        idxs = sorted({int(i) for i in (page_indices if page_indices is not None else range(n)) if 0 <= int(i) < n})
        prev = None
        for i in idxs:
            page = doc.load_page(i)
            text = page.get_text("text") or ""
            if doi is None and i < 3:
                doi = find_doi(text)
            info = classify_page(text, _image_coverage(page), cfg)
            info.page = i + 1
            infos.append(info)
            if info.label == "procedure":
                blocks = [(i + 1, b) for b in _blocks(page)]
                if prev is not None and prev == i - 1 and runs:
                    runs[-1].extend(blocks)      # consecutive pages: one run, chunks may span the break
                else:
                    runs.append(blocks)
                prev = i
            if info.label == "scanned":
                units.append(Unit(f"{pid}:i{len(units)}", "procedure", "image", [i + 1], png=render(page, max_side)))
            if info.has_table and info.label in ("procedure", "other", "scanned"):
                units.append(Unit(f"{pid}:t{len(units)}", "table", "image", [i + 1], png=render(page, max_side)))
    for run in runs:
        for ch in chunk_blocks(run, max_chars):
            pages = sorted({p for p, _ in ch})
            text = "\n\n".join(b for _, b in ch)
            units.append(Unit(f"{pid}:c{len(units)}", "procedure", "text", pages, text=text))
    return infos, units, {"paper_id": pid, "doi": doi, "n_pages": n}
