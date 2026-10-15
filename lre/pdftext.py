"""PDF helpers: positioned text lines, page images, age/sex from the header."""
import re
import pymupdf


def _doc(b: bytes):
    return pymupdf.open(stream=b, filetype="pdf")


def has_text(b: bytes) -> bool:
    try:
        return sum(len(p.get_text().strip()) for p in _doc(b)) > 50
    except Exception:
        return False


def lines(b: bytes) -> list[str]:
    out = []
    for page in _doc(b):
        rows = {}
        for w in page.get_text("words"):
            rows.setdefault(round((w[1] + w[3]) / 2 / 3), []).append(w)
        for key in sorted(rows):
            out.append(" ".join(w[4] for w in sorted(rows[key], key=lambda w: w[0])))
    return out


def page_pngs(b: bytes, dpi: int = 150) -> list[bytes]:
    return [p.get_pixmap(dpi=dpi).tobytes("png") for p in _doc(b)]


def patient_from_text(ls: list[str]) -> dict:
    for l in ls:
        if m := re.search(r"(\d{1,3})\s*Y(?:rs?|ears?)\.?\s*/\s*([MF])\b", l, re.I):
            return {"age": int(m[1]), "sex": m[2].upper()}
    return {"age": None, "sex": None}
