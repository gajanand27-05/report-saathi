"""Gemma reads a report page into raw rows. Values are verified against the PDF text layer."""
import base64, json, re
from . import pdftext

PROMPT = """Read this lab report page. Return ONLY JSON: {"rows": [...], "lab_notes": [...]}.
Each row: {"test": name as printed, "value": result exactly as printed (string), "unit": unit as printed,
"ranges": [{"label": ..., "text": ...}]}.
- One row per test result. Skip "Method" lines, Interpretation, Limitations, Notes, References, and week-by-week tables.
- ranges: one entry per printed reference line. label = the words before the range ("Adults", "TSH (2nd Trimester)",
  "1- =<6 Years", "Deficiency"); text = the range itself ("0.27-4.20", "<20"). If a line starts with the range
  ("<200: Desirable"), put the whole line in text and leave label empty. If ranges are printed in Pregnant /
  Non-Pregnant sections, prefix the label: "Pregnant - Normal", "Non-Pregnant - Impaired Fasting Glucose".
- Copy units exactly, even if they look wrong. Write "N/R" if the lab printed N/R.
- lab_notes: short comments written by the lab, as "<Field>: <text>" (e.g. "Impression: ...", "RBC Morphology: ...").
- Never guess a value you can't read; leave that row out."""


def _image_msg(png: bytes, extra: str) -> list:
    url = "data:image/png;base64," + base64.b64encode(png).decode()
    return [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                         {"type": "text", "text": PROMPT + extra}]}]


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        d = {}
    return {"rows": d.get("rows", []) or [], "lab_notes": d.get("lab_notes", []) or []}


def verify(rows: list, ls: list[str]) -> list:
    joined = " " + " ".join(ls) + " "
    out = []
    for r in rows:
        v = str(r.get("value", "")).strip()
        ok = v.upper() == "N/R" or re.search(r"(?<![\d.])" + re.escape(v) + r"(?![\d])", joined) is not None
        out.append(r if ok else dict(r, value=None, unread=True))
    return out


def read(file_bytes: bytes, filename: str, llm) -> dict:
    is_pdf = file_bytes[:4] == b"%PDF"
    if is_pdf and pdftext.has_text(file_bytes):
        pages = pdftext.page_lines(file_bytes)
        ls = [l for p in pages for l in p]
        rows, notes = [], []
        for png, page_ls in zip(pdftext.page_pngs(file_bytes), pages):  # each page with its OWN text only
            d = _parse(llm(_image_msg(png, "\n\nThis page's exact text, for copying numbers:\n" + "\n".join(page_ls))))
            rows += d["rows"]; notes += d["lab_notes"]
        patient = pdftext.patient_from_text(ls)
        return {"patient": patient, "rows": verify(_dedupe(rows), ls), "lab_notes": list(dict.fromkeys(notes)),
                "needs_confirm": patient["age"] is None or patient["sex"] is None}
    pngs = pdftext.page_pngs(file_bytes) if is_pdf else [file_bytes]
    rows, notes = [], []
    for png in pngs:
        d = _parse(llm(_image_msg(png, ""))); rows += d["rows"]; notes += d["lab_notes"]
    return {"patient": {"age": None, "sex": None}, "rows": _dedupe(rows), "lab_notes": list(dict.fromkeys(notes)),
            "needs_confirm": True}


def _dedupe(rows):
    seen, out = set(), []
    for r in rows:
        k = (r.get("test"), str(r.get("value")))
        if k not in seen:
            seen.add(k); out.append(r)
    return out
