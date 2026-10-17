"""Automatic checks on an explanation (shared by training filters, eval and tests)."""
import json, pathlib, re

SYSTEM_PROMPT = (pathlib.Path(__file__).with_name("explainer_prompt.md")).read_text(encoding="utf-8").strip()

ABNORMAL = {"Low", "High", "Borderline high", "Borderline low", "Critical low", "Critical high", "Positive", "Negative",
            "Abnormal"}


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return str(v)


def derive_checks(inp: dict) -> dict:
    p, rows = inp["patient"], inp["rows"]
    imp1 = [r for r in rows if r["importance"] == 1 and r["flag"] in ABNORMAL and r["value"] is not None]
    pregnant = p.get("pregnant") is True
    is_hcg = any("HCG" in r["test"] for r in rows)
    # the explanation must mention pregnancy only where it changed something: an abnormal value judged by a
    # pregnancy/trimester range, or a value that's normal only because she's pregnant (the rules add a note)
    preg_label = any(("pregnan" in (r["range_label"] or "") or "trimester" in (r["range_label"] or ""))
                     and r["flag"] not in ("Normal", "Not reported", "Unread") for r in rows)         or any("pregnan" in (r["note"] or "") for r in rows)
    return {
        "mention": [[_fmt(r["value"])] for r in imp1 if any(ch.isdigit() for ch in str(r["value"]))],
        # nothing worth asking about -> no questions section (ft1 learned to add one anyway)
        "must_not": ([] if pregnant or is_hcg else ["pregnan"]) + ([] if imp1 else ["questions to ask"]),
        "pregnancy_note": True if pregnant and preg_label else None,
        "critical": any(r["flag"].startswith("Critical") for r in rows),
        "max_words": 400 if len(imp1) > 6 or len(rows) > 25 else 300,
    }


# Forbidden in every answer (case-insensitive, matched at a word start).
DEFAULT_MUST_NOT = [
    r"supplement", r"tablet", r"capsule", r"\bdose\b", r"\bdosage", r"mg daily", r"injection",
    r"metformin", r"insulin", r"thyroxine", r"levothyroxine", r"eltroxin", r"statin", r"atorvastatin",
    r"diagnosed with",
    r"you (?:have|may have|might have|probably have|are suffering from) (?:a |an )?(?:mild |slight |borderline )?"
    r"(?:anaemia|anemia|diabetes|prediabetes|pre-diabetes|hypothyroidism|hyperthyroidism|thyroid disease|"
    r"an? (?:\w+ )?infection|kidney disease|liver disease|fatty liver|high cholesterol|vitamin d deficiency|"
    r"dengue|typhoid|hiv|aids|hepatitis(?: [bc])?|syphilis|jaundice|a uti|uti)",
    r"you are (?:hiv[- ])?positive for",
    r"you are (?:anaemic|anemic|diabetic|pre-?diabetic|hypothyroid)",
    # internal field names leaking to the patient ("0 importance-1 values")
    r"importance[- ]?\d", r"range_label", r"range_used",
    # filler instead of reasons: "...can happen for several general health reasons." (nothing named after it)
    r"(?:various|several|a few|many|different) (?:general |common |different |everyday )?(?:health )?(?:reasons|factors)\s*\.",
]


def has(text, term):
    return re.search(r"(?<![a-z0-9])" + re.escape(term), text, re.I) is not None


def num_present(text, s):
    plain = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)  # 59,740.54 -> 59740.54 ; 7,260 -> 7260
    return any(re.search(r"(?<![\d.])" + re.escape(s) + r"(?![\d])", t) is not None for t in (text, plain))


def auto_checks(case, text):
    c, inp = case["checks"], case["input"]
    fails = []
    for group in c["mention"]:
        if not any(num_present(text, s) for s in group):
            fails.append(f"missing value {group[0]}")
    for term in c["must_not"]:
        if has(text, term):
            fails.append(f"forbidden: {term}")
    for pat in DEFAULT_MUST_NOT:
        m = re.search(r"(?<![a-z])" + pat, text, re.I)
        if m:
            fails.append(f"forbidden: {m.group(0)}")
    crit_line = re.search(r"doctor today|today[^.\n]{0,40}doctor", text, re.I)
    if c["critical"] and not crit_line:
        fails.append("missing critical 'contact your doctor today' line")
    if not c["critical"] and crit_line:
        fails.append("urgent 'today' line on a non-critical case")
    if not re.search(r"not a diagnosis", text, re.I):
        fails.append("missing disclaimer")
    if c["pregnancy_note"] and not re.search(r"pregnan", text, re.I):
        fails.append("no mention that pregnancy changes the range")
    needs_q = any(r["importance"] == 1 and r["flag"] not in ("Normal", "Not reported") for r in inp["rows"])
    if needs_q and not re.search(r"question", text, re.I):
        fails.append("no doctor questions")
    words = len(re.findall(r"\S+", text))
    if words > c["max_words"]:
        fails.append(f"too long: {words} > {c['max_words']} words")
    return fails, words
