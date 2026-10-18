import base64, importlib, os
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'t.db'}")
    monkeypatch.setenv("WORKER_TOKEN", "secret")
    import app.main as m; importlib.reload(m)
    return TestClient(m.app)

W = {"Authorization": "Bearer secret"}

def test_full_pdf_flow(client):
    r = client.post("/api/jobs", files={"file": ("r.pdf", b"%PDF-1.4 fake", "application/pdf")},
                    data={"pregnant": "yes", "weeks": "26", "shop": "ravi"})
    jid = r.json()["id"]
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "queued"
    assert client.get("/api/worker/next").status_code == 401
    job = client.get("/api/worker/next", headers=W).json()
    assert job["stage"] == "read" and base64.b64decode(job["file_b64"]).startswith(b"%PDF")
    assert job["pregnant"] is True and job["weeks"] == 26
    assert client.get("/api/worker/next", headers=W).status_code == 204  # job taken (by this worker)
    client.post(f"/api/worker/{jid}/rows", headers=W, json={"patient": {"age": 25, "sex": "F"}, "rows": [], "lab_notes": [], "needs_confirm": False})
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "explaining"
    client.post(f"/api/worker/{jid}/text", headers=W, json={"text": "Overview..."})
    assert client.get(f"/api/jobs/{jid}").json()["text"] == "Overview..."
    client.post(f"/api/worker/{jid}/done", headers=W, json={"text": "Final."})
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "done"
    assert client.get("/api/stats", params={"shop": "ravi"}).json()["reports"] == 1

def test_photo_confirm_flow(client):
    jid = client.post("/api/jobs", files={"file": ("p.jpg", b"\xff\xd8\xff fake", "image/jpeg")}, data={"pregnant": "no"}).json()["id"]
    client.get("/api/worker/next", headers=W)
    client.post(f"/api/worker/{jid}/rows", headers=W, json={"patient": {"age": None, "sex": None}, "rows": [{"test": "Hb", "value": "9", "unit": "g/dL", "ranges": []}], "lab_notes": [], "needs_confirm": True})
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "confirm"
    client.post(f"/api/jobs/{jid}/confirm", json={"patient": {"age": 30, "sex": "F"}, "rows": [{"test": "Hb", "value": "9.5", "unit": "g/dL", "ranges": []}]})
    job = client.get("/api/worker/next", headers=W).json()
    assert job["stage"] == "explain" and job["rows"][0]["value"] == "9.5" and job["patient"]["age"] == 30

def test_rejects_big_and_bad_files(client):
    assert client.post("/api/jobs", files={"file": ("x.txt", b"hello", "text/plain")}, data={"pregnant": "no"}).status_code == 400
    big = b"%PDF" + b"0" * (10 * 1024 * 1024 + 1)
    assert client.post("/api/jobs", files={"file": ("b.pdf", big, "application/pdf")}, data={"pregnant": "no"}).status_code == 413

def test_cleanup(client, tmp_path):
    import app.db as dbm, time
    jid = client.post("/api/jobs", files={"file": ("r.pdf", b"%PDF-1.4", "application/pdf")}, data={"pregnant": "no"}).json()["id"]
    db = dbm.connect(os.environ["DATABASE_URL"])
    db.execute("UPDATE jobs SET created_at = ? WHERE id = ?", (time.time() - 25 * 3600, jid))
    assert db.delete_older_than(24 * 3600) == 1
    assert client.get(f"/api/jobs/{jid}").status_code == 404

def test_pages(client):
    assert "Upload" in client.get("/").text
    assert client.get("/r/abc").status_code == 200
    assert "Example" in client.get("/example").text
    ex = client.get("/api/example").json()
    assert ex["synthetic"] is True and ex["rows"] and "not a diagnosis" in ex["text"]


def test_worker_poll_cleans_up_old_jobs(client):
    import app.db as dbm, app.main as m, time
    jid = client.post("/api/jobs", files={"file": ("r.pdf", b"%PDF-1.4", "application/pdf")}, data={"pregnant": "no"}).json()["id"]
    dbm.connect(os.environ["DATABASE_URL"]).execute("UPDATE jobs SET created_at = ? WHERE id = ?", (time.time() - 25 * 3600, jid))
    m._last_cleanup = 0.0
    assert client.get("/api/worker/next", headers=W).status_code == 204   # the old job was deleted, not handed out
    assert client.get(f"/api/jobs/{jid}").status_code == 404


def test_file_kept_until_read_and_stuck_jobs_requeued(client):
    import app.db as dbm, time
    db = dbm.connect(os.environ["DATABASE_URL"])
    jid = client.post("/api/jobs", files={"file": ("r.pdf", b"%PDF-1.4 x", "application/pdf")}, data={"pregnant": "no"}).json()["id"]
    assert client.get("/api/worker/next", headers=W).json()["id"] == jid          # claimed, e.g. by a worker that then crashes
    assert db.get(jid)["file"] is not None                                         # file kept while reading
    db.execute("UPDATE jobs SET picked_at = ? WHERE id = ?", (time.time() - 700, jid))
    again = client.get("/api/worker/next", headers=W).json()                       # stale -> back in the queue
    assert again["id"] == jid and again["file_b64"]
    client.post(f"/api/worker/{jid}/rows", headers=W, json={"patient": {"age": 30, "sex": "F"}, "rows": [], "lab_notes": [], "needs_confirm": False})
    assert db.get(jid)["file"] is None                                             # wiped once read


def test_health_and_queue_position(client):
    assert client.get("/api/health").json()["worker"] == "offline"          # no worker has polled yet
    a = client.post("/api/jobs", files={"file": ("a.pdf", b"%PDF a", "application/pdf")}, data={"pregnant": "no"}).json()["id"]
    b = client.post("/api/jobs", files={"file": ("b.pdf", b"%PDF b", "application/pdf")}, data={"pregnant": "no"}).json()["id"]
    jb = client.get(f"/api/jobs/{b}").json()
    assert jb["ahead"] == 1 and jb["worker"] == "offline" and jb["age_seconds"] >= 0
    client.get("/api/worker/next", headers=W)                                # the worker polls (and takes job a)
    assert client.get("/api/health").json()["worker"] == "ready"
    assert client.get(f"/api/jobs/{b}").json()["ahead"] == 0


def test_critical_banner_comes_from_the_rules(client):
    jid = client.post("/api/jobs", files={"file": ("c.pdf", b"%PDF c", "application/pdf")}, data={"pregnant": "yes", "weeks": "32"}).json()["id"]
    client.get("/api/worker/next", headers=W)
    rows = [{"test": "Hemoglobin", "value": "6.2", "unit": "g/dL", "ranges": [{"label": "", "text": "12.0-15.0"}]},
            {"test": "TSH", "value": "2.1", "unit": "uIU/mL", "ranges": [{"label": "", "text": "0.27-4.2"}]}]
    assert client.get(f"/api/jobs/{jid}").json()["critical"] == []          # not read yet
    client.post(f"/api/worker/{jid}/rows", headers=W, json={"patient": {"age": 27, "sex": "F"}, "rows": rows, "lab_notes": [], "needs_confirm": False})
    # the model's text never mentions it, yet the page still knows: the flag comes from rules.py
    client.post(f"/api/worker/{jid}/text", headers=W, json={"text": "Overview: one value."})
    assert client.get(f"/api/jobs/{jid}").json()["critical"] == ["Haemoglobin"]


def test_example_text_is_in_the_page_itself(client):
    import json, pathlib
    ex = json.loads((pathlib.Path(__file__).parents[1] / "app/static/example.json").read_text(encoding="utf-8"))
    page = client.get("/example").text
    assert "Loading…" not in page and ex["text"].split("\n")[0][:40] in page and 'id="ex"' in page


def test_shop_poster(client):
    from app.main import shop_code
    assert shop_code("Ravi Medicals, MG Road!") == "ravi-medicals-mg-road"
    page = client.get("/shop", params={"name": "Ravi Medicals <b>"}).text
    assert "<svg" in page and "?shop=ravi-medicals-b" in page and "Ravi Medicals &lt;b&gt;" in page and "<b>Ravi" not in page
    client.post("/api/jobs", files={"file": ("r.pdf", b"%PDF x", "application/pdf")}, data={"pregnant": "no", "shop": "ravi-medicals-b"})
    assert "(1 so far)" in client.get("/shop", params={"name": "Ravi Medicals <b>"}).text
    assert "<svg" in client.get("/shop").text                                  # no name yet: plain link, still a poster
