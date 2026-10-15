from lre.ranges import Interval, parse_interval, split_range, classify_label

def test_dash_range():
    assert parse_interval("12.0-15.0") == Interval(12.0, 15.0)
    assert parse_interval("36 - 46") == Interval(36, 46)
    assert parse_interval("150–450") == Interval(150, 450)

def test_one_sided():
    assert parse_interval("<40") == Interval(None, 40, hi_incl=False)
    assert parse_interval("=<5.6") == Interval(None, 5.6)
    assert parse_interval("up to 0.9") == Interval(None, 0.9)
    assert parse_interval("upto 35") == Interval(None, 35)
    assert parse_interval("> 90 mL/min/1.73 m2") == Interval(90, None, lo_incl=False)
    assert parse_interval("=>240") == Interval(240, None)

def test_bare_number_means_up_to():
    assert parse_interval("0.30") == Interval(None, 0.30)

def test_unparseable():
    assert parse_interval("Refer Interpretation") is None
    assert parse_interval("Negative") is None

def test_contains_and_scale():
    i = Interval(None, 40, hi_incl=False)
    assert i.contains(39.9) and not i.contains(40)
    assert Interval(150, 450).scaled(0.01) == Interval(1.5, 4.5)

def test_text():
    assert Interval(12.0, 15.0).text() == "12-15"
    assert Interval(None, 40, hi_incl=False).text() == "<40"
    assert Interval(90, None, lo_incl=False).text() == ">90"

def test_split_range_value_first_and_label_first():
    assert split_range("", "<200: Desirable") == ("Desirable", "<200")
    assert split_range("", "Non-Diabetic: <=5.6") == ("Non-Diabetic", "<=5.6")
    assert split_range("Adults", "0.27-4.20") == ("Adults", "0.27-4.20")
    assert split_range("", "< 5.3 : NEGATIVE") == ("NEGATIVE", "< 5.3")

def test_classify():
    assert classify_label("TSH (2nd Trimester)")["n"] == 2
    assert classify_label("Pregnant - Normal") == {"kind": "band", "band": "Normal", "section": "pregnant"}
    assert classify_label("Non-Pregnant - Impaired Fasting Glucose")["section"] == "nonpregnant"
    assert classify_label("Adults")["kind"] == "adult"
    a = classify_label(">6- =<11 Years")
    assert a["kind"] == "age" and a["age"].contains(8) and not a["age"].contains(30)
    assert classify_label("0 - 50 Years")["age"].contains(46)
    assert classify_label("Desirable") == {"kind": "band", "band": "Desirable", "section": None}
    assert classify_label("")["kind"] == "plain"
