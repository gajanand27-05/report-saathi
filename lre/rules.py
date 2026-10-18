"""Deterministic flagging: raw rows from the reader + patient -> explainer input. No AI here."""
import csv, re
from dataclasses import dataclass
from pathlib import Path

from .ranges import Interval, classify_label, parse_interval, split_range

# The lab prints e.g. "373 lakhs/cmm" for 373 thousand/cmm. The value we pass on is in lakh (3.73), so the note
# must say both numbers: "read as thousand" next to 3.73 would mean 3,730, a dangerously low count.
TYPO_NOTE = ("the lab printed {printed} 'lakhs/cmm', which is a printing mistake: it means {printed} thousand/cmm, "
             "which is the same as {value} lakh/cmm")
FLAGS = ("Normal", "Low", "High", "Borderline high", "Borderline low", "Critical low", "Critical high",
         "Positive", "Negative", "Abnormal", "Not reported", "Unread")
NOT_REPORTED = {"n/r", "nr", "not reported", "-", "--", ""}
# What a positive screening result does and doesn't mean (never a diagnosis)
SCREEN_NOTE = ("this is a screening test; a positive screening result is checked with a confirmatory test, "
               "and your doctor will explain what it means")
POSITIVE_NOTES = {"HBSAG": SCREEN_NOTE, "HCV": SCREEN_NOTE, "HIV": SCREEN_NOTE, "VDRL": SCREEN_NOTE,
                  "DNS1": "please show this result to your doctor soon; only a doctor can say what it means together with your symptoms",
                  "DIGM": "please show this result to your doctor soon; only a doctor can say what it means together with your symptoms",
                  "DIGG": "a positive IgG often means an infection some time in the past; your doctor will explain it with your symptoms"}
WIDAL_NOTE = "the Widal test can be raised for reasons other than typhoid, so your doctor will decide what it means"
CULTURE_NOTE = ("the lab grew bacteria from the sample; your doctor will explain what this means and decide "
                "about any treatment (the lab's list of medicines is for your doctor)")
RH_NOTE ="an Rh negative blood group matters in pregnancy; your doctor will tell you what, if anything, is needed"


@dataclass
class Rule:
    id: str; display: str; aliases: list; unit: str; kind: str; importance: int; minor_pct: float
    range_f: str; range_m: str; preg: dict; crit_low: float | None; crit_high: float | None
    min_possible: float | None; max_possible: float | None; alt_scale: float | None; normal_text: list


def _f(s):
    return float(s) if s not in ("", None) else None


def _norm(s: str) -> str:
    s = s.lower().replace("–", "-").replace("—", "-")
    s = re.sub(r"[^a-z0-9/()%+ -]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _load():
    rules = {}
    with open(Path(__file__).with_name("rules.csv"), encoding="utf-8") as fh:
        for d in csv.DictReader(fh):
            rules[d["id"]] = Rule(
                d["id"], d["display"], [_norm(a) for a in d["aliases"].split("|")] + [_norm(d["display"])], d["unit"], d["kind"],
                int(d["importance"]), float(d["minor_pct"] or 0), d["range_f"], d["range_m"],
                {1: d["preg_t1"], 2: d["preg_t2"], 3: d["preg_t3"]}, _f(d["crit_low"]), _f(d["crit_high"]),
                _f(d["min_possible"]), _f(d["max_possible"]), _f(d["alt_scale"]),
                [x for x in d["normal_text"].split("|") if x])
    return rules


RULES = _load()
_ALIASES = sorted(((a, r) for r in RULES.values() for a in r.aliases), key=lambda x: -len(x[0]))


def match_rule(name: str) -> Rule | None:
    n = _norm(name)
    for alias, rule in _ALIASES:
        if n == alias or n.replace(" ", "") == alias.replace(" ", ""):
            return rule
        rest = n[len(alias):].strip() if n.startswith(alias + " ") else None
        # "Total cholesterol / HDL ratio" must not match "total cholesterol"; a ratio alias may take a trailing "ratio"
        if rest is not None and ("/" in alias or (not rest.startswith("/") and "ratio" not in rest)):
            return rule
    return None


def trimester(weeks) -> int | None:
    if weeks in (None, ""):
        return None
    w = float(weeks)
    return 1 if w < 14 else 2 if w < 28 else 3


def _plain_number(x) -> bool:
    return isinstance(x, (int, float)) or re.fullmatch(r"\d+(?:\.\d+)?", str(x).replace(",", "").strip()) is not None


def _value(v):
    """Return float, or (lo, hi) mean for '15-20', or the original string for text values."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if m := re.fullmatch(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", s):
        return (float(m[1]) + float(m[2])) / 2
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)
    return s


# band name -> flag family. Order matters: 'insufficiency' before 'sufficiency', 'non-diabetic' before 'diabetic'.
_BAND = [("border", "B"), ("pre", "B"), ("impaired", "B"), ("insufficien", "B"),
         ("non-diabetic", "N"), ("non diabetic", "N"), ("normal", "N"), ("desirable", "N"), ("optimal", "N"),
         ("sufficien", "N"), ("negative", "N"),
         ("low", "L"), ("deficien", "L"),
         ("high", "H"), ("diabet", "H"), ("toxic", "H"), ("positive", "H")]


def _band_family(name: str) -> str:
    n = name.lower()
    for key, fam in _BAND:
        if key in n:
            return fam
    return "N"


def _choose(rule: Rule, ranges: list, patient: dict):
    """Return (kind, payload, label). kind 'interval' -> Interval; 'bands' -> [(name, Interval)]."""
    items = []
    for r in ranges:
        label, text = split_range(r.get("label", ""), r.get("text", ""))
        iv = parse_interval(text)
        if iv:
            items.append((label, classify_label(label), iv))
    preg = patient.get("pregnant") is True
    tri = trimester(patient.get("weeks")) if preg else None
    age = patient.get("age")

    if preg:
        t = [i for i in items if i[1]["kind"] == "trimester" and i[1]["n"] == tri]
        if t:
            return "interval", t[0][2], f"lab range, {_ord(tri)} trimester"
        p = [i for i in items if i[1].get("section") == "pregnant" or i[1]["kind"] == "pregnant"]
        if p:
            return _bands_or_interval(p, "lab range, pregnant")
    rest = [i for i in items if i[1]["kind"] not in ("trimester", "pregnant") and i[1].get("section") != "pregnant"]
    nonpreg = [i for i in rest if i[1].get("section") == "nonpregnant" or i[1]["kind"] == "nonpregnant"]
    if nonpreg:
        rest = nonpreg
    ages = [i for i in rest if i[1]["kind"] == "age"]
    if ages and age is not None:
        hit = [i for i in ages if i[1]["age"].contains(float(age))]
        if hit:
            return "interval", hit[0][2], "lab range, age " + hit[0][0].strip()
    adult = [i for i in rest if i[1]["kind"] == "adult"]
    if adult and (age is None or float(age) >= 18):
        return "interval", adult[0][2], "lab range, adults"
    rest = [i for i in rest if i[1]["kind"] in ("plain", "band", "nonpregnant")]
    lab_label = "lab range" + (", non-pregnant" if nonpreg else "")
    if preg and rule.preg.get(tri):
        printed = ", ".join(i[2].text() for i in rest) or "none"
        return ("interval", parse_interval(rule.preg[tri]),
                f"pregnancy range, {_ord(tri)} trimester (our table; lab printed only the non-pregnant {printed})")
    if rest:
        return _bands_or_interval(rest, lab_label)
    fallback = rule.range_m if str(patient.get("sex", "")).upper().startswith("M") else rule.range_f
    if fallback:
        return "interval", parse_interval(fallback), "our table"
    return None, None, ""


def _bands_or_interval(items, label):
    named = [(i[1]["band"], i[2]) for i in items if i[1]["kind"] == "band"]
    if len(named) >= 2:
        return "bands", named, label
    return "interval", items[0][2], label


def _ord(n):
    return {1: "1st", 2: "2nd", 3: "3rd"}[n]


def _flag_interval(v, iv: Interval):
    if iv.contains(v):
        return "Normal"
    return "Low" if iv.lo is not None and v < iv.lo else "High"


def _flag_bands(v, bands):
    note = None
    hit = [b for b in bands if b[1].contains(v)]
    if hit:
        name, iv = hit[0]
    else:
        def dist(b):
            iv = b[1]
            return min(abs(v - x) for x in (iv.lo, iv.hi) if x is not None)
        name, iv = min(bands, key=dist)
        others = " and ".join(f"{iv2.text()} {n2}" for n2, iv2 in bands if n2 != name and dist((n2, iv2)) < 2 * dist((name, iv)) + 1e-9)
        note = f"{v:g} falls between the lab's bands ({iv.text()} {name}; {others}); it is at the boundary"
    fam = _band_family(name)
    normal = next((b[1] for b in bands if _band_family(b[0]) == "N"), None)
    above = normal is not None and normal.hi is not None and v > normal.hi
    flag = {"N": "Normal", "L": "Low", "H": "High",
            "B": "Borderline high" if above or normal is None else "Borderline low"}[fam]
    return flag, f"{iv.text()} = {name}", note


def _posneg(v, rule: Rule, ranges: list, patient: dict) -> dict:
    """Screening tests (HIV, hepatitis, VDRL, dengue) and beta-HCG: Negative or Positive, from text or an index value."""
    if isinstance(v, str):
        negative = any(w in v.lower() for w in rule.normal_text)
        used = "Negative"
    else:
        kind, payload, label = _choose(rule, ranges, patient)
        if kind == "bands":
            hit = [b for b in payload if b[1].contains(v)] or sorted(
                payload, key=lambda b: min(abs(v - x) for x in (b[1].lo, b[1].hi) if x is not None))
            negative = re.search(r"neg|non", hit[0][0], re.I) is not None
            neg_band = next((b for b in payload if re.search(r"neg|non", b[0], re.I)), None)
            used = f"{neg_band[1].text()} = negative" if neg_band else ""
        elif kind == "interval":  # a single printed range is the negative range ("< 5.3 : NEGATIVE")
            negative, used = payload.contains(v), f"{payload.text()} = negative"
        else:
            return {"flag": "Not reported", "note": "no reference range found"}
    note = None if negative else POSITIVE_NOTES.get(rule.id)
    # a negative screening test is simply normal; beta-HCG keeps "Negative" (it matters if she thinks she's pregnant)
    neg_flag = "Normal" if rule.kind == "sero" else "Negative"
    return {"flag": neg_flag if negative else "Positive", "range_used": used, "note": note}


def _titre(rv, ranges: list, rule: Rule) -> dict:
    """Widal: '1:160' against '1:80 & Above Significant'. 'No Agglutination' is negative."""
    s = str(rv).lower()
    if any(w in s for w in rule.normal_text):
        return {"flag": "Normal", "range_used": "no agglutination"}
    m = re.search(r"1\s*:\s*(\d+)", s)
    if not m:
        return {"flag": "Not reported", "note": "we couldn't read this result"}
    cut = next((int(c[1]) for r in ranges if (c := re.search(r"1\s*:\s*(\d+)", r.get("text", "")))), 160)
    positive = int(m[1]) >= cut
    out = {"flag": "Positive" if positive else "Normal", "range_used": f"1:{cut} and above = significant",
           "note": WIDAL_NOTE if positive else None}
    if positive:
        out["importance"] = 1  # a positive infection test is never a "small difference", even for AH/BH
    return out


def _normal_for_pregnancy_note(v, rule: Rule, ranges: list, patient: dict):
    """A value that's normal for pregnancy but outside the usual (non-pregnant) range: say so, or it looks wrong."""
    kind, payload, _ = _choose(rule, ranges, dict(patient, pregnant=False, weeks=None))
    if kind == "interval":
        flag, used = _flag_interval(v, payload), payload.text()
    elif kind == "bands":
        flag, used, _ = _flag_bands(v, payload)
    else:
        return None
    if flag == "Normal":
        return None
    word = "low" if "low" in flag.lower() else "high"
    return f"normal for this stage of pregnancy; the usual non-pregnant range ({used}) would call it {word}"


def _minor(v, iv: Interval, pct: float) -> bool:
    if pct <= 0 or iv is None:
        return False
    edge = iv.lo if iv.lo is not None and v < iv.lo else iv.hi
    return edge not in (None, 0) and abs(v - edge) / abs(edge) < pct


def evaluate(raw_rows: list, patient: dict, lab_notes=()) -> dict:
    # The report's own sex wins over the upload form: someone may upload a family member's report and answer the
    # pregnancy question about themselves. A male report is never judged by pregnancy ranges.
    if str(patient.get("sex") or "").upper().startswith("M"):
        patient = dict(patient, pregnant=False, weeks=None)
    out = []
    for raw in raw_rows:
        name, unit = raw.get("test", ""), raw.get("unit", "") or ""
        rule = match_rule(name)
        v = _value(raw.get("value"))
        rv = raw.get("value")
        # Urine routine prints bare names ("Glucose", "RBC", "Blood"): a text result or an /HPF unit means urine
        reported = not (isinstance(v, str) and v.lower() in NOT_REPORTED) and rv is not None
        if reported and not name.lower().startswith("urine") and \
                (rule is None or (rule.kind in ("num", "calc") and (isinstance(v, str) or "hpf" in unit.lower()))):
            rule = match_rule("urine " + name) or rule
        if rule is not None and rule.kind == "skip":  # e.g. antibiotic lists of a culture: never shown
            continue
        row = {"test": rule.display if rule else name, "value": v if isinstance(v, float) and _plain_number(rv) else rv,
               "unit": unit, "range_used": "", "range_label": "", "flag": "Normal",
               "importance": rule.importance if rule else 1, "note": None}
        out.append(row)
        if raw.get("unread"):
            row.update(flag="Unread", value=None, note="we couldn't read this value reliably; check it on the printed report")
            continue
        if rule is None:
            row.update(flag="Not reported", range_used="not in our table",
                       note="this test is not in our rule table, so we have not checked it; ask your doctor")
            continue
        if isinstance(v, str) and v.lower() in NOT_REPORTED or raw.get("value") is None:
            row.update(flag="Not reported", value=None)
            continue
        if rule.kind == "info":  # blood group, Rh: nothing to flag
            row.update(flag="Normal", importance=2)
            if rule.id == "RHGRP" and "neg" in str(rv).lower() and patient.get("pregnant") is True:
                row.update(importance=1, note=RH_NOTE)
            continue
        if rule.kind == "culture":
            grown = not any(w in str(rv).lower() for w in rule.normal_text)
            row.update(flag="Positive" if grown else "Normal", range_used="no growth",
                       note=CULTURE_NOTE if grown else None)
            continue
        if rule.kind == "titre":
            row.update(**_titre(rv, raw.get("ranges", []), rule))
            continue
        if rule.kind in ("sero", "posneg"):
            row.update(**_posneg(v, rule, raw.get("ranges", []), patient))
            continue
        if rule.kind == "text" or isinstance(v, str):
            normal_words = rule.normal_text
            vv = v.lower().strip() if isinstance(v, str) else ""
            # exact, or the result STARTS with a normal word ("Negative (-)", "Straw coloured"); "Dark Yellow" is not "yellow"
            ok = any(vv == t or (vv.startswith(t) and not vv[len(t):len(t) + 1].isalpha()) for t in normal_words)
            detect = any(w in normal_words for w in ("negative", "nil", "absent"))
            flag = "Normal" if ok else ("Positive" if detect else "Abnormal")
            if rule.kind == "num" and not ok and isinstance(v, str):
                flag = "High"  # e.g. urine RBC "Plenty" against a numeric range
            row.update(flag=flag, range_used="/".join(normal_words).title() or "")
            if flag == "Positive" and "trace" in str(rv).lower():
                row["importance"] = 2
            continue
        kind, payload, label = _choose(rule, raw.get("ranges", []), patient)
        if rule.alt_scale and rule.max_possible is not None and v > rule.max_possible \
                and rule.min_possible <= v / rule.alt_scale <= rule.max_possible and "lakh" in unit.lower():
            printed = raw.get("value")
            v = round(v / rule.alt_scale, 4)
            row.update(value=v, unit=rule.unit, note=TYPO_NOTE.format(printed=printed, value=v))
            if kind == "interval" and payload.lo is not None and payload.lo > rule.max_possible:
                payload = payload.scaled(1 / rule.alt_scale)
        if (rule.min_possible is not None and v < rule.min_possible) or (rule.max_possible is not None and v > rule.max_possible):
            row.update(flag="Unread", value=None, note="the value we read is not possible for this test; check it on the printed report")
            continue
        row["range_label"] = label
        if kind == "bands":
            flag, used, note = _flag_bands(v, payload)
            row.update(flag=flag, range_used=used, note=note or row["note"])
            iv = next((b[1] for b in payload if _band_family(b[0]) == "N"), None)
        elif kind == "interval":
            iv = payload
            row.update(flag=_flag_interval(v, iv), range_used=iv.text())
        else:
            row.update(flag="Not reported", note="no reference range found")
            continue
        if row["flag"] == "Normal" and patient.get("pregnant") is True and \
                ("pregnan" in label or "trimester" in label):
            row["note"] = row["note"] or _normal_for_pregnancy_note(v, rule, raw.get("ranges", []), patient)
        if rule.crit_low is not None and v < rule.crit_low:
            row["flag"] = "Critical low"
        elif rule.crit_high is not None and v > rule.crit_high:
            row["flag"] = "Critical high"
        if rule.kind == "calc":
            row["importance"] = 2
        elif row["flag"] in ("Low", "High") and _minor(v, iv, rule.minor_pct):
            row["importance"] = 2
    return {"patient": patient, "rows": out, "lab_notes": list(lab_notes)}
