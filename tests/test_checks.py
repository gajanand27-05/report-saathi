from lre.checks import auto_checks, derive_checks, SYSTEM_PROMPT

INP = {"patient": {"age": 51, "sex": "F", "pregnant": False, "weeks": None},
       "rows": [{"test": "TSH", "value": 8.71, "unit": "uIU/mL", "range_used": "0.27-4.2",
                 "range_label": "lab range, adults", "flag": "High", "importance": 1, "note": None}],
       "lab_notes": []}

def test_derive_checks():
    c = derive_checks(INP)
    assert c == {"mention": [["8.71"]], "must_not": ["pregnan"], "pregnancy_note": None,
                 "critical": False, "max_words": 300}

def test_good_and_bad_answers():
    case = {"input": INP, "checks": derive_checks(INP)}
    good = ("1 value needs a look. TSH (a signal from the brain to the thyroid gland) is 8.71, high for adults.\n"
            "Questions to ask your doctor: Do I need more thyroid tests?\n"
            "This explains your report. It is not a diagnosis. Please show this report to your doctor.")
    assert auto_checks(case, good)[0] == []
    bad = "You have hypothyroidism. Take thyroxine tablets. Contact your doctor today."
    fails = auto_checks(case, bad)[0]
    assert any("thyroxine" in f for f in fails) and any("You have hypothyroidism" in f for f in fails)
    assert "missing disclaimer" in fails and any("today" in f for f in fails)

def test_pregnancy_note_only_when_it_matters():
    row = {"test": "TSH", "value": 2.1, "unit": "uIU/mL", "range_used": "0.35-4.1",
           "range_label": "lab range, 2nd trimester", "flag": "Normal", "importance": 1, "note": None}
    inp = {"patient": {"age": 28, "sex": "F", "pregnant": True, "weeks": 20}, "rows": [row], "lab_notes": []}
    assert derive_checks(inp)["pregnancy_note"] is None          # all normal: nothing to say
    inp["rows"] = [dict(row, value=4.4, flag="High")]
    assert derive_checks(inp)["pregnancy_note"] is True          # abnormal by the trimester range
    inp["rows"] = [dict(row, note="normal for this stage of pregnancy; the usual non-pregnant range (0.27-4.2) would call it high")]
    assert derive_checks(inp)["pregnancy_note"] is True          # normal only because she's pregnant


def test_jargon_and_filler_fail():
    case = {"input": INP, "checks": derive_checks(INP)}
    base = ("TSH (a signal from the brain to the thyroid gland) is 8.71, high for adults. {x}\n"
            "Questions to ask your doctor: Do I need more thyroid tests?\n"
            "This explains your report. It is not a diagnosis. Please show this report to your doctor.")
    assert auto_checks(case, base.format(x="It can be higher in the early morning or after illness."))[0] == []
    assert any("importance" in f for f in auto_checks(case, base.format(x="That is 1 importance-1 value."))[0])
    assert any("reasons" in f for f in auto_checks(case, base.format(x="This can happen for several general health reasons."))[0])
    # naming the reasons after "a few common reasons" is fine
    assert auto_checks(case, base.format(x="This can happen for a few common reasons, such as illness or the time of day."))[0] == []


def test_number_formats():
    from lre.checks import _fmt, num_present
    assert _fmt(59740.54) == "59740.54" and _fmt(8.4) == "8.4" and _fmt(7260.0) == "7260"
    assert num_present("your count is 59,740.54 today", "59740.54")
    assert num_present("WBC 7,260 cells", "7260") and not num_present("value 18.4", "8.4")


def test_prompt_loaded():
    assert SYSTEM_PROMPT.startswith("You explain a lab report")


def test_all_normal_report_has_no_questions_section():
    row = dict(INP["rows"][0], value=2.1, flag="Normal")
    inp = dict(INP, rows=[row])
    assert "questions to ask" in derive_checks(inp)["must_not"]
    assert "questions to ask" not in derive_checks(INP)["must_not"]
