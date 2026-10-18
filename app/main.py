import base64, os, pathlib, time
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from . import db as dbm

MAX = 10 * 1024 * 1024
STATIC = pathlib.Path(__file__).with_name("static")
app = FastAPI(docs_url=None, redoc_url=None)
DB = dbm.connect(os.environ.get("DATABASE_URL", "sqlite:///local.db"))
TOKEN = os.environ.get("WORKER_TOKEN", "")


def kind(data: bytes):
    if data[:4] == b"%PDF": return "pdf"
    if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n": return "image"
    return None


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/example")
def example_page():
    return FileResponse(STATIC / "example.html")


@app.get("/api/example")
def example_data():
    # a finished explanation of a SYNTHETIC report, served by the web app alone: works even when no worker is running
    return FileResponse(STATIC / "example.json", media_type="application/json")


@app.get("/r/{jid}")
def result_page(jid: str):
    return FileResponse(STATIC / "result.html")


@app.post("/api/jobs")
async def create(file: UploadFile = File(...), pregnant: str = Form(...), weeks: str = Form(""), shop: str = Form("")):
    data = await file.read(MAX + 1)
    if len(data) > MAX:
        raise HTTPException(413, "File is larger than 10 MB")
    if not kind(data):
        raise HTTPException(400, "Please upload a PDF or a photo (JPG/PNG)")
    p = {"yes": "yes", "no": "no"}.get(pregnant, "unsure")
    w = int(weeks) if weeks.strip().isdigit() and p == "yes" else None
    return {"id": DB.create_job(shop.strip()[:40] or None, file.filename, data, p, w)}


def public(j):
    return {k: j.get(k) for k in ("status", "patient", "rows", "text", "error")}


@app.get("/api/jobs/{jid}")
def status(jid: str):
    j = DB.get(jid)
    if not j:
        raise HTTPException(404, "Not found (results are deleted after 24 hours)")
    return public(j)


@app.post("/api/jobs/{jid}/confirm")
async def confirm(jid: str, req: Request):
    j = DB.get(jid)
    if not j or j["status"] != "confirm":
        raise HTTPException(409, "Nothing to confirm")
    body = await req.json()
    DB.update(jid, status="confirmed", patient=body["patient"], rows=body["rows"])
    return {"ok": True}


@app.get("/api/stats")
def stats(shop: str):
    return {"shop": shop, "reports": DB.shop_count(shop)}


_last_cleanup = 0.0


def cleanup_if_due(every=600):
    """Delete jobs older than 24 h, at most every 10 min. Runs on the worker's polls, so no cron service is needed."""
    global _last_cleanup
    if time.time() - _last_cleanup >= every:
        _last_cleanup = time.time()
        DB.delete_older_than(24 * 3600)


def auth(h):
    if not TOKEN or h != f"Bearer {TOKEN}":
        raise HTTPException(401, "bad token")


@app.get("/api/worker/next")
def worker_next(authorization: str = Header("")):
    auth(authorization)
    cleanup_if_due()
    j = DB.take_next()
    if not j:
        return Response(status_code=204)
    preg = {"yes": True, "no": False}.get(j["pregnant"])
    return {"id": j["id"], "stage": "read" if j["status"] == "queued" else "explain",
            "file_b64": base64.b64encode(j["file"]).decode() if j["file"] is not None else None,
            "filename": j["filename"], "pregnant": preg, "weeks": j["weeks"],
            "patient": j.get("patient"), "rows": j.get("rows"), "lab_notes": j.get("lab_notes") or []}


@app.post("/api/worker/{jid}/rows")
async def worker_rows(jid: str, req: Request, authorization: str = Header("")):
    auth(authorization); b = await req.json()
    DB.update(jid, status="confirm" if b.get("needs_confirm") else "explaining", file=None,  # read: wipe the file now
              patient=b["patient"], rows=b["rows"], lab_notes=b.get("lab_notes", []))
    return {"ok": True}


@app.post("/api/worker/{jid}/text")
async def worker_text(jid: str, req: Request, authorization: str = Header("")):
    auth(authorization); DB.update(jid, text=(await req.json())["text"]); return {"ok": True}


@app.post("/api/worker/{jid}/done")
async def worker_done(jid: str, req: Request, authorization: str = Header("")):
    auth(authorization); DB.update(jid, status="done", file=None, text=(await req.json())["text"]); return {"ok": True}


@app.post("/api/worker/{jid}/error")
async def worker_error(jid: str, req: Request, authorization: str = Header("")):
    auth(authorization); DB.update(jid, status="error", file=None, error=(await req.json())["message"]); return {"ok": True}
