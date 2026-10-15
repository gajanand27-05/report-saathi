"""Generate synthetic explainer inputs: lab-style raw rows -> lre.rules.evaluate -> input + derived checks.
python training/make_cases.py --n 3000 --seed 7 --out data/cases.jsonl"""
import argparse, json, pathlib, random, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lre.checks import derive_checks
from lre.rules import RULES, evaluate
from lre.ranges import parse_interval

PANELS = {
    "cbc": ["HB", "RBC", "HCT", "MCV", "MCH", "MCHC", "RDWCV", "RDWSD", "WBC", "NEUT", "LYMPH", "EOS", "MONO", "BASO",
            "ANC", "ALC", "AMC", "AEC", "ABC", "PLT", "MPV", "PDW", "PCT", "PLCR"],
    "sugar": ["GLUF", "HBA1C", "EAG"], "random_sugar": ["GLUR"], "ogtt": ["GLUF", "GLU1H", "GLU2H", "USUG"],
    "lipid": ["CHOL", "TG", "HDL", "LDL", "VLDL", "CHOLHDL", "LDLHDL"],
    "lft": ["BILT", "BILD", "BILI", "ALP", "ALT", "AST", "GGT", "TP", "ALB", "GLOB", "AGR"],
    "kft": ["CREA", "UREA", "BUN", "BUNCR", "NA", "K", "CL", "UA", "EGFR"],
    "thyroid": ["T3", "T4", "TSH"], "tsh": ["TSH"], "ft4": ["TSH", "FT4"],
    "iron": ["IRON", "UIBC", "TIBC", "TSAT", "FERR"], "vit": ["VITD", "B12"], "minerals": ["CA", "MG"],
    "hcg": ["HCG"], "urine": ["UPUS", "UPROT", "USUG"], "esr": ["ESR"],
}
GENERAL = ["cbc", "sugar", "lipid", "lft", "kft", "thyroid", "tsh", "iron", "vit", "minerals", "urine", "esr", "random_sugar"]
ANTENATAL = ["cbc", "ogtt", "thyroid", "tsh", "ft4", "iron", "urine", "hcg"]
BANDS = {  # lab-style band printing for some tests
    "HBA1C": [("", "Non-Diabetic: <=5.6"), ("", "Pre Diabetic:5.7-6.4"), ("", "Diabetic: >=6.5")],
    "CHOL": [("", "<200: Desirable"), ("", "200-239: Borderline"), ("", "=>240: High")],
    "TG": [("", "<150: Normal"), ("", "150-199: Borderline-High"), ("", "200-499: High"), ("", "=>500: Very High")],
    "HDL": [("", "<40: Low"), ("", "40 - 59: Optimal"), ("", "=>60: Desirable")],
    "LDL": [("", "<100: Normal"), ("", "100 - 129: Desirable"), ("", "130 - 159: Borderline-High"), ("", "160 - 189: High"), ("", "=>190: Very High")],
    "VITD": [("Deficiency", "<20"), ("Insufficiency", "21-29"), ("Sufficiency", "30-99"), ("Potential Toxicity", "=>100")],
    "GLUF": [("Non-Pregnant - Normal", "70-100"), ("Non-Pregnant - Impaired Fasting Glucose", "101-125"),
             ("Non-Pregnant - Diabetes Mellitus", ">=126"), ("Pregnant - Normal", "<92"),
             ("Pregnant - Gestational Diabetes Mellitus", ">=92")],
}
AGE_TRI = {  # tests labs print with age + trimester lines
    "TSH": [("1- =<6 Years", "0.70-5.97"), (">6- =<11 Years", "0.60-4.84"), (">11- =<20 Years", "0.51-4.30"),
            ("Adults", "0.27-4.20"), ("TSH (1st Trimester)", "0.33-4.59"), ("TSH (2nd Trimester)", "0.35-4.10"),
            ("TSH (3rd Trimester)", "0.21-3.15")],
    "T3": [("Adults", "80-200"), ("T3 (1st Trimester)", "105-230"), ("T3 (2nd Trimester)", "129-262"), ("T3 (3rd Trimester)", "135-262")],
    "T4": [("Adults", "5.10-14.1"), ("T4 (1st Trimester)", "7.33-14.8"), ("T4 (2nd Trimester)", "7.93-16.1"), ("T4 (3rd Trimester)", "6.95-15.7")],
}
NOTES = ["Impression: S/O Normal Peripheral Smear", "RBC Morphology: Normocytic Normochromic cells seen.",
         "Impression: S/O Microcytic Hypochromic Anaemia", "RBC Morphology: Microcytic Hypochromic cells seen.",
         "Advise: Kindly correlate clinically", "Platelet Morphology: Adequate"]
UNKNOWN = [("Anti-Mullerian Hormone (AMH)", "ng/mL", "2.0-6.8", (0.1, 8)), ("Prolactin", "ng/mL", "4.8-23.3", (2, 80)),
           ("CRP (C-Reactive Protein)", "mg/L", "<6", (0.5, 60)), ("Homocysteine", "umol/L", "5-15", (4, 40))]


def sample_value(rng, rule, sex, preg_tri, abnormal_rate):
    rtext = rule.range_m if sex == "M" else rule.range_f
    if preg_tri and rule.preg.get(preg_tri):
        rtext = rule.preg[preg_tri]
    iv = parse_interval(rtext) if rtext else None
    hi = iv.hi if iv and iv.hi is not None else None
    lo = iv.lo if iv and iv.lo is not None else (hi * 0.65 if hi else (rule.min_possible or 0))
    hi = hi if hi is not None else (lo * 1.6 if lo else 1)
    span = hi - lo
    r = rng.random()
    if r > abnormal_rate:
        v = rng.uniform(lo + 0.05 * span, hi - 0.05 * span)
    else:
        sev = rng.choices(["mild", "moderate", "critical"], [0.65, 0.3, 0.05])[0]
        if sev == "critical" and (rule.crit_low is not None or rule.crit_high is not None):
            v = rule.crit_low * rng.uniform(0.6, 0.95) if rule.crit_low is not None and (rule.crit_high is None or rng.random() < 0.5) \
                else rule.crit_high * rng.uniform(1.05, 1.4)
        else:
            k = {"mild": (0.01, 0.12), "moderate": (0.15, 0.6), "critical": (0.6, 1.0)}[sev]
            if rng.random() < 0.5 and lo > 0:
                v = max(lo - span * rng.uniform(*k), lo * rng.uniform(0.2, 0.5))
            else:
                v = hi + span * rng.uniform(*k)
    lo_ok, hi_ok = rule.min_possible or 0, rule.max_possible or 1e9
    v = min(max(v, lo_ok * 1.01 if lo_ok else 0), hi_ok * 0.99)
    return round(v, 3 if v < 1 else 2 if v < 10 else 1 if v < 100 else 0)


def raw_ranges(rule, sex):
    if rule.id in BANDS:
        return [{"label": l, "text": t} for l, t in BANDS[rule.id]]
    if rule.id in AGE_TRI:
        return [{"label": l, "text": t} for l, t in AGE_TRI[rule.id]]
    rt = rule.range_m if sex == "M" else rule.range_f
    return [{"label": "", "text": rt}] if rt else []


def make_case(rng, i):
    preg = rng.random() < 0.4
    sex = "F" if preg or rng.random() < 0.55 else "M"
    age = rng.randint(19, 40) if preg else rng.choice([rng.randint(5, 17)] + [rng.randint(18, 85)] * 9)
    weeks = rng.randint(5, 39) if preg else None
    tri = (1 if weeks < 14 else 2 if weeks < 28 else 3) if preg else None
    pregnant = True if preg else rng.choices([False, None], [0.9, 0.1])[0]
    panels = rng.sample(ANTENATAL if preg else GENERAL, k=rng.choice([1, 1, 2, 2, 3, 4]))
    if not preg and "hcg" not in panels and sex == "F" and 18 <= age <= 45 and rng.random() < 0.05:
        panels.append("hcg")
    abnormal_rate = rng.choice([0.05, 0.15, 0.3, 0.5])
    ids = list(dict.fromkeys(t for p in panels for t in PANELS[p]))
    raws = []
    for tid in ids:
        rule = RULES[tid]
        if rule.kind == "text":
            val = rng.choices(["Nil", "Trace", "+", "++"], [0.75, 0.12, 0.08, 0.05])[0]
            raws.append({"test": rule.display, "value": val, "unit": "", "ranges": []}); continue
        if rule.kind == "posneg":
            val = rng.choice([round(rng.uniform(0.5, 4.9), 2), round(rng.uniform(50, 150000), 2)])
            raws.append({"test": rule.display, "value": str(val), "unit": rule.unit,
                         "ranges": [{"label": "", "text": "< 5.3 : NEGATIVE"}]}); continue
        v = sample_value(rng, rule, sex, tri, abnormal_rate)
        unit, text = rule.unit, str(v)
        if tid == "PLT" and rng.random() < 0.15:  # the real lab's unit typo
            unit, text = "lakhs/cmm", str(round(v * 100))
            ranges = [{"label": "", "text": "150-450"}]
        else:
            ranges = raw_ranges(rule, sex)
        if rng.random() < 0.02:
            text = "N/R"
        raws.append({"test": rule.aliases[0].title() if rng.random() < 0.5 else rule.display,
                     "value": text, "unit": unit, "ranges": ranges})
    if rng.random() < 0.05:
        name, unit, rt, (a, b) = rng.choice(UNKNOWN)
        raws.append({"test": name, "value": str(round(rng.uniform(a, b), 2)), "unit": unit, "ranges": [{"label": "", "text": rt}]})
    if rng.random() < 0.04 and raws:
        k = rng.randrange(len(raws)); raws[k] = dict(raws[k], value="9999")  # impossible -> Unread
    notes = rng.sample(NOTES, k=rng.choice([1, 2])) if "cbc" in panels and rng.random() < 0.3 else []
    inp = evaluate(raws, {"age": age, "sex": sex, "pregnant": pregnant, "weeks": weeks}, notes)
    return {"id": f"syn{i:05d}", "input": inp, "checks": derive_checks(inp)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3000); ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="data/cases.jsonl")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    out = pathlib.Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for i in range(a.n):
            fh.write(json.dumps(make_case(rng, i), ensure_ascii=False) + "\n")
    print("wrote", a.n, "->", out)


if __name__ == "__main__":
    main()
