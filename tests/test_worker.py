import base64, json
from worker.worker import handle

class FakeApi:
    def __init__(self): self.calls = []
    def post(self, path, body): self.calls.append((path, body))

ROWS = {"rows": [{"test": "Hemoglobin (Hb)", "value": "8.4", "unit": "g/dL", "ranges": [{"label": "", "text": "12.0-15.0"}]}], "lab_notes": []}

def test_pdf_job_goes_read_rules_explain(monkeypatch):
    import lre.reader as rd
    monkeypatch.setattr(rd, "read", lambda b, fn, llm: {"patient": {"age": 25, "sex": "F"}, **ROWS, "needs_confirm": False})
    api, seen = FakeApi(), {}
    def explain(inp):
        seen["inp"] = inp; yield "Hello "; yield "world."
    job = {"id": "j1", "stage": "read", "file_b64": base64.b64encode(b"%PDF").decode(), "filename": "r.pdf",
           "pregnant": True, "weeks": 26, "patient": None, "rows": None, "lab_notes": []}
    handle(job, api, reader_llm=None, explain_stream=explain)
    paths = [p for p, _ in api.calls]
    assert paths[0] == "/api/worker/j1/rows" and paths[-1] == "/api/worker/j1/done"
    assert api.calls[-1][1]["text"] == "Hello world."
    r = seen["inp"]["rows"][0]
    assert r["flag"] == "Low" and "2nd trimester" in r["range_label"]

def test_photo_job_stops_for_confirmation(monkeypatch):
    import lre.reader as rd
    monkeypatch.setattr(rd, "read", lambda b, fn, llm: {"patient": {"age": None, "sex": None}, **ROWS, "needs_confirm": True})
    api = FakeApi()
    handle({"id": "j2", "stage": "read", "file_b64": base64.b64encode(b"\xff\xd8\xff").decode(), "filename": "p.jpg",
            "pregnant": False, "weeks": None, "patient": None, "rows": None, "lab_notes": []}, api, None, lambda inp: iter(()))
    assert [p for p, _ in api.calls] == ["/api/worker/j2/rows"] and api.calls[0][1]["needs_confirm"] is True

def test_errors_are_reported(monkeypatch):
    import lre.reader as rd
    def boom(*a): raise RuntimeError("model down")
    monkeypatch.setattr(rd, "read", boom)
    api = FakeApi()
    handle({"id": "j3", "stage": "read", "file_b64": "", "filename": "x.pdf", "pregnant": None, "weeks": None,
            "patient": None, "rows": None, "lab_notes": []}, api, None, None)
    assert api.calls[-1][0] == "/api/worker/j3/error"
    assert "model down" not in api.calls[-1][1]["message"]  # no internals shown to users
