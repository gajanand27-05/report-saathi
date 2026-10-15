from lre.rules import evaluate, match_rule, trimester, TYPO_NOTE

def row(test, value, unit="", *ranges):
    return {"test": test, "value": value, "unit": unit,
            "ranges": [{"label": l, "text": t} for l, t in ranges]}

P = lambda age=30, sex="F", preg=False, weeks=None: {"age": age, "sex": sex, "pregnant": preg, "weeks": weeks}

def one(raw, patient):
    return evaluate([raw], patient)["rows"][0]

def test_match_rule_aliases():
    assert match_rule("Hemoglobin (Hb)").id == "HB"
    assert match_rule("Glucose – Fasting").id == "GLUF"
    assert match_rule("Blood Glucose 2 hr").id == "GLU2H"
    assert match_rule("Thyroid Stimulating Hormone (TSH)").id == "TSH"
    assert match_rule("Anti-Mullerian Hormone") is None

def test_trimester():
    assert (trimester(9), trimester(20), trimester(30), trimester(None)) == (1, 2, 3, None)

def test_simple_low_and_normal():
    r = one(row("Hemoglobin (Hb)", "13.4", "g/dL", ("", "12.0-15.0")), P())
    assert (r["flag"], r["range_used"], r["range_label"]) == ("Normal", "12-15", "lab range")
    assert one(row("Hb", "11.0", "g/dL", ("", "12.0-15.0")), P())["flag"] == "Low"

def test_pregnancy_uses_our_table_when_lab_has_no_pregnancy_range():
    r = one(row("Hemoglobin (Hb)", "8.7", "g/dL", ("", "12.0-15.0")), P(25, "F", True, 26))
    assert r["flag"] == "Low" and r["range_used"] == ">=10.5"
    assert "2nd trimester" in r["range_label"] and "our table" in r["range_label"]
    n = one(row("Hb", "11.2", "g/dL", ("", "12.0-15.0")), P(24, "F", True, 22))
    assert n["flag"] == "Normal"

def test_lab_trimester_and_age_bands():
    tsh = [("1- =<6 Years", "0.70-5.97"), (">6- =<11 Years", "0.60-4.84"), (">11- =<20 Years", "0.51-4.30"),
           ("Adults", "0.27-4.20"), ("TSH (1st Trimester)", "0.33-4.59"), ("TSH (2nd Trimester)", "0.35-4.10"),
           ("TSH (3rd Trimester)", "0.21-3.15")]
    r = one(row("TSH", "8.71", "uIU/mL", *tsh), P(51))
    assert (r["flag"], r["range_used"], r["range_label"]) == ("High", "0.27-4.2", "lab range, adults")
    r = one(row("TSH", "3.6", "uIU/mL", *tsh), P(30, "F", True, 31))
    assert (r["flag"], r["range_label"]) == ("High", "lab range, 3rd trimester")
    r = one(row("TSH", "4.5", "uIU/mL", *tsh), P(8))
    assert (r["flag"], r["range_used"]) == ("Normal", "0.6-4.84")

def test_bands_and_borderline():
    bands = [("", "Non-Diabetic: <=5.6"), ("", "Pre Diabetic:5.7-6.4"), ("", "Diabetic: >=6.5")]
    r = one(row("HbA1c", "6.3", "%", *bands), P(46))
    assert r["flag"] == "Borderline high" and r["range_used"] == "5.7-6.4 = Pre Diabetic"
    assert one(row("HbA1c", "7.1", "%", *bands), P(46))["flag"] == "High"
    hdl = [("", "<40: Low"), ("", "40 - 59: Optimal"), ("", "=>60: Desirable")]
    assert one(row("Cholesterol - HDL", "54", "mg/dL", *hdl), P(46))["flag"] == "Normal"
    assert one(row("Cholesterol - HDL", "36", "mg/dL", *hdl), P(46))["flag"] == "Low"

def test_band_gap_goes_to_nearest_with_note():
    vd = [("Deficiency", "<20"), ("Insufficiency", "21-29"), ("Sufficiency", "30-99"), ("Potential Toxicity", "=>100")]
    r = one(row("25-OH, Vitamin D Total", "20.6", "ng/mL", *vd), P(46))
    assert r["flag"] == "Borderline low" and "between" in r["note"]

def test_ogtt_pregnant_section():
    rng = [("Non-Pregnant - Normal", "70-100"), ("Non-Pregnant - Impaired Fasting Glucose", "101-125"),
           ("Pregnant - Normal", "<92"), ("Pregnant - Gestational Diabetes Mellitus", ">=92")]
    assert one(row("Glucose Fasting", "86.2", "mg/dL", *rng), P(25, "F", True, 26))["flag"] == "Normal"
    assert one(row("Glucose Fasting", "94", "mg/dL", *rng), P(25, "F", True, 26))["flag"] == "High"
    assert one(row("Glucose Fasting", "112.3", "mg/dL", *rng), P(46))["flag"] == "Borderline high"

def test_platelet_unit_typo_fixed_with_note():
    r = one(row("Platelet Count", "368", "lakhs/cmm", ("", "150-450")), P())
    assert (r["value"], r["flag"], r["note"]) == (3.68, "Normal", TYPO_NOTE)

def test_impossible_value_is_unread():
    r = one(row("Hemoglobin", "84", "g/dL", ("", "12-15")), P())
    assert (r["flag"], r["value"]) == ("Unread", None)

def test_critical_overrides():
    r = one(row("Potassium", "6.4", "mmol/L", ("", "3.5-5.1")), P(55))
    assert r["flag"] == "Critical high"

def test_minor_pct_downgrades_importance():
    assert one(row("Mean Cell Volume (MCV)", "81.9", "fl", ("", "83-101")), P(46))["importance"] == 2
    assert one(row("Mean Cell Volume (MCV)", "61.2", "fl", ("", "83-101")), P(25))["importance"] == 1

def test_posneg_and_text_and_not_reported_and_unknown():
    assert one(row("Beta HCG", "1834.2", "mIU/mL", ("", "< 5.3 : NEGATIVE")), P(29))["flag"] == "Positive"
    assert one(row("Beta HCG", "2.1", "mIU/mL", ("", "< 5.3 : NEGATIVE")), P(29))["flag"] == "Negative"
    assert one(row("Urine Glucose Fasting", "Nil", ""), P())["flag"] == "Normal"
    assert one(row("Urine protein", "Trace", ""), P())["flag"] == "High"
    assert one(row("Blood Glucose 1 hr", "N/R", "mg/dL", ("", "<180")), P())["flag"] == "Not reported"
    u = one(row("Anti-Mullerian Hormone (AMH)", "0.9", "ng/mL", ("", "2-6.8")), P())
    assert u["flag"] == "Not reported" and "not in our rule table" in u["note"]

def test_value_range_like_pus_cells():
    r = one(row("Pus cells", "15-20", "/hpf", ("", "0-5")), P())
    assert (r["flag"], r["value"]) == ("High", "15-20")

def test_reader_unread_rows_stay_unread():
    r = evaluate([{"test": "Hb", "value": None, "unit": "g/dL", "ranges": [], "unread": True}], P())["rows"][0]
    assert r["flag"] == "Unread"

def test_output_shape():
    out = evaluate([row("Hb", "13", "g/dL", ("", "12-15"))], P(), ["Impression: Normal smear"])
    assert set(out) == {"patient", "rows", "lab_notes"} and out["lab_notes"] == ["Impression: Normal smear"]
    assert set(out["rows"][0]) == {"test", "value", "unit", "range_used", "range_label", "flag", "importance", "note"}
    assert out["rows"][0]["test"] == "Haemoglobin"
