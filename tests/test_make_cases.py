import json, subprocess, sys, pathlib
from lre.rules import FLAGS

def test_generator(tmp_path):
    out = tmp_path / "c.jsonl"
    subprocess.run([sys.executable, "training/make_cases.py", "--n", "300", "--seed", "1", "--out", str(out)],
                   check=True, cwd=pathlib.Path(__file__).resolve().parent.parent)
    cases = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert len(cases) == 300 and len({c["id"] for c in cases}) == 300
    flags = [r["flag"] for c in cases for r in c["input"]["rows"]]
    assert set(flags) <= set(FLAGS)
    for f in ("Normal", "High", "Low", "Borderline high", "Critical low", "Positive", "Unread", "Not reported"):
        assert f in flags, f
    preg = sum(c["input"]["patient"]["pregnant"] is True for c in cases)
    assert 0.3 * 300 <= preg <= 0.5 * 300
    assert any(r["note"] and "lakhs" in r["note"] for c in cases for r in c["input"]["rows"])
    assert all("checks" in c and "mention" in c["checks"] for c in cases)
