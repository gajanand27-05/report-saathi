import json, pymupdf
from lre import pdftext, reader

def make_pdf(lines):
    doc = pymupdf.open(); page = doc.new_page()
    for i, (x_text) in enumerate(lines):
        page.insert_text((40, 60 + 18 * i), x_text, fontsize=10)
    return doc.tobytes()

PDF = make_pdf(["Age/Gender : 25 Yrs. / F", "Hemoglobin (Hb) : 8.4 g/dL 12.0-15.0", "Platelet Count : 373 lakhs/cmm 150-450"])

def test_pdftext():
    assert pdftext.has_text(PDF)
    ls = pdftext.lines(PDF)
    assert any("8.4" in l for l in ls)
    assert pdftext.patient_from_text(ls) == {"age": 25, "sex": "F"}
    assert pdftext.page_pngs(PDF)[0][:4] == b"\x89PNG"

def fake_llm(answer):
    return lambda messages: json.dumps(answer)

def test_read_pdf_and_hallucination_check():
    ans = {"rows": [{"test": "Hemoglobin (Hb)", "value": "8.4", "unit": "g/dL", "ranges": [{"label": "", "text": "12.0-15.0"}]},
                    {"test": "Platelet Count", "value": "373", "unit": "lakhs/cmm", "ranges": [{"label": "", "text": "150-450"}]},
                    {"test": "WBC", "value": "7260", "unit": "cells/cmm", "ranges": []}],
           "lab_notes": []}
    out = reader.read(PDF, "r.pdf", fake_llm(ans))
    assert out["patient"] == {"age": 25, "sex": "F"} and out["needs_confirm"] is False
    vals = {r["test"]: r["value"] for r in out["rows"]}
    assert vals["Hemoglobin (Hb)"] == "8.4" and vals["WBC"] is None  # 7260 is not in the PDF -> unread

def test_photo_needs_confirm():
    png = pdftext.page_pngs(PDF)[0]
    out = reader.read(png, "photo.png", fake_llm({"rows": [], "lab_notes": []}))
    assert out["needs_confirm"] is True


def test_pages_read_in_parallel_keep_order():
    import threading, time
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page().insert_text((40, 60), f"Age/Gender : 30 Yrs. / F   Test{i} : {i + 1}.5 g/dL", fontsize=10)
    pdf = doc.tobytes()
    active, peak = [0], [0]
    lock = threading.Lock()

    def llm(messages):
        text = messages[0]["content"][1]["text"]
        i = int(text.split("Test")[1][0])
        with lock:
            active[0] += 1; peak[0] = max(peak[0], active[0])
        time.sleep(0.2)
        with lock:
            active[0] -= 1
        return json.dumps({"rows": [{"test": f"Test{i}", "value": f"{i + 1}.5", "unit": "g/dL", "ranges": []}], "lab_notes": []})

    out = reader.read(pdf, "r.pdf", llm)
    assert [r["test"] for r in out["rows"]] == ["Test0", "Test1", "Test2"] and peak[0] > 1
