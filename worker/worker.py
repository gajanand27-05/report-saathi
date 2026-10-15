"""DGX worker: poll the Render app, read -> rules -> explain, stream text back. Outbound HTTPS only.
APP_URL=https://... WORKER_TOKEN=... READER_URL=http://127.0.0.1:8301/v1 READER_MODEL=gemma-31b \
EXPLAINER_URL=http://127.0.0.1:8300/v1 EXPLAINER_MODEL=ft1 python worker/worker.py"""
import base64, json, os, pathlib, sys, time, traceback, urllib.error, urllib.request
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lre import explainer, reader
from lre.llm import chat
from lre.rules import evaluate

USER_ERROR = "We couldn't process this report. If it's a photo, please retake it flat and in good light."


class Api:
    def __init__(self, base, token):
        self.base, self.h = base.rstrip("/"), {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def next(self):
        req = urllib.request.Request(self.base + "/api/worker/next", headers=self.h)
        with urllib.request.urlopen(req, timeout=30) as r:
            return None if r.status == 204 else json.load(r)

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), self.h, method="POST")
        urllib.request.urlopen(req, timeout=30).close()


def handle(job, api, reader_llm, explain_stream):
    jid = job["id"]
    try:
        if job["stage"] == "read":
            data = base64.b64decode(job["file_b64"] or "")
            got = reader.read(data, job["filename"], reader_llm)
            api.post(f"/api/worker/{jid}/rows", {"patient": got["patient"], "rows": got["rows"],
                                                 "lab_notes": got["lab_notes"], "needs_confirm": got["needs_confirm"]})
            if got["needs_confirm"]:
                return
            patient, rows, notes = got["patient"], got["rows"], got["lab_notes"]
        else:
            patient, rows, notes = job["patient"], job["rows"], job.get("lab_notes") or []
        patient = dict(patient, pregnant=job["pregnant"], weeks=job["weeks"])
        inp = evaluate(rows, patient, notes)  # rows the reader marked "unread" become flag Unread
        text, last = "", 0.0
        for chunk in explain_stream(inp):
            text += chunk
            if time.time() - last > 1.0:
                api.post(f"/api/worker/{jid}/text", {"text": text}); last = time.time()
        api.post(f"/api/worker/{jid}/done", {"text": text})
    except Exception:
        traceback.print_exc()  # job id only; never print report content
        api.post(f"/api/worker/{jid}/error", {"message": USER_ERROR})


def main():
    api = Api(os.environ["APP_URL"], os.environ["WORKER_TOKEN"])
    r_url, r_model = os.environ["READER_URL"], os.environ["READER_MODEL"]
    e_url, e_model = os.environ["EXPLAINER_URL"], os.environ["EXPLAINER_MODEL"]
    reader_llm = lambda msgs: chat(r_url, r_model, msgs, max_tokens=4000, temperature=0.0)
    explain = lambda inp: explainer.stream(inp, e_url, e_model)
    print("worker up", flush=True)
    while True:
        try:
            job = api.next()
        except (urllib.error.URLError, TimeoutError) as e:
            print("poll failed:", type(e).__name__, flush=True); time.sleep(10); continue
        if job is None:
            time.sleep(2); continue
        t = time.time(); handle(job, api, reader_llm, explain)
        print(f"job {job['id'][:8]} {job['stage']} {time.time() - t:.1f}s", flush=True)


if __name__ == "__main__":
    main()
