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
    assert client.get("/api/worker/next", headers=W).status_code == 204  # file is gone, job taken
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


def test_worker_poll_cleans_up_old_jobs(client):
    import app.db as dbm, app.main as m, time
    jid = client.post("/api/jobs", files={"file": ("r.pdf", b"%PDF-1.4", "application/pdf")}, data={"pregnant": "no"}).json()["id"]
    dbm.connect(os.environ["DATABASE_URL"]).execute("UPDATE jobs SET created_at = ? WHERE id = ?", (time.time() - 25 * 3600, jid))
    m._last_cleanup = 0.0
    assert client.get("/api/worker/next", headers=W).status_code == 204   # the old job was deleted, not handed out
    assert client.get(f"/api/jobs/{jid}").status_code == 404
