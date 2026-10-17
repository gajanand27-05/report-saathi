"""Teacher (31B) writes explanations for synthetic cases; keep only those passing auto checks + judge.
python training/teacher.py --cases data/cases.jsonl --url http://127.0.0.1:8301/v1 --model gemma-31b \
       --judge-prompt ../dgx/judge_prompt.md --workers 10
After a crash, run the same command again: finished cases (done.jsonl) are skipped, kept ones (kept.jsonl) are reused."""
import argparse, concurrent.futures as cf, json, pathlib, random, re, sys, threading
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lre.checks import SYSTEM_PROMPT, auto_checks
from lre.llm import chat


def judge(url, model, jprompt, inp, text):
    out = chat(url, model, [{"role": "system", "content": jprompt},
                            {"role": "user", "content": "INPUT JSON:\n" + json.dumps(inp, ensure_ascii=False)
                             + "\n\nEXPLANATION TO GRADE:\n" + text}], max_tokens=700, temperature=0.0)
    m = re.search(r"\{.*\}", out, re.S)
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"pass": False, "reasons": ["judge JSON invalid"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True); ap.add_argument("--url", required=True); ap.add_argument("--model", required=True)
    ap.add_argument("--judge-prompt", required=True); ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--tries", type=int, default=2); ap.add_argument("--val-frac", type=float, default=0.05)
    ap.add_argument("--outdir", default="data")
    a = ap.parse_args()
    jprompt = pathlib.Path(a.judge_prompt).read_text(encoding="utf-8")
    cases = [json.loads(l) for l in open(a.cases, encoding="utf-8")]
    out = pathlib.Path(a.outdir); out.mkdir(parents=True, exist_ok=True)
    log = (out / "teacher_log.jsonl").open("a", encoding="utf-8")
    # Checkpointing: every finished case goes to done.jsonl, every kept example to kept.jsonl, as it happens.
    # Re-running the same command after a crash skips finished cases and keeps what was already kept.
    done_f, kept_f = out / "done.jsonl", out / "kept.jsonl"
    done = {json.loads(l)["id"] for l in open(done_f, encoding="utf-8")} if done_f.exists() else set()
    kept = [json.loads(l) for l in open(kept_f, encoding="utf-8")] if kept_f.exists() else []
    todo = [c for c in cases if c["id"] not in done]
    if done:
        print(f"resuming: {len(done)} cases already done, {len(kept)} kept, {len(todo)} to go", flush=True)
    lock = threading.Lock()
    done_w, kept_w = done_f.open("a", encoding="utf-8"), kept_f.open("a", encoding="utf-8")

    def work(case):
        user = json.dumps(case["input"], ensure_ascii=False)
        for t in range(a.tries):
            text = chat(a.url, a.model, [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
                        temperature=0.4 + 0.2 * t)
            fails, _ = auto_checks(case, text)
            j = judge(a.url, a.model, jprompt, case["input"], text) if not fails else {"pass": False, "reasons": ["skipped"]}
            rec = {"id": case["id"], "try": t, "auto_fails": fails, "judge": j}
            log.write(json.dumps(rec, ensure_ascii=False) + "\n"); log.flush()
            if not fails and j.get("pass"):
                return {"id": case["id"], "input": case["input"], "target": text.strip()}
        return None

    def work_and_save(case):
        r = work(case)
        with lock:
            if r:
                kept_w.write(json.dumps(r, ensure_ascii=False) + "\n"); kept_w.flush()
            done_w.write(json.dumps({"id": case["id"], "kept": bool(r)}) + "\n"); done_w.flush()
        return r

    with cf.ThreadPoolExecutor(a.workers) as ex:
        for i, r in enumerate(ex.map(work_and_save, todo), 1):
            if r: kept.append(r)
            if i % 100 == 0: print(f"{len(done) + i}/{len(cases)} kept {len(kept)}", flush=True)
    kept.sort(key=lambda x: x["id"])  # deterministic split whatever the finishing order
    random.Random(0).shuffle(kept)
    nval = min(max(20, int(len(kept) * a.val_frac)), len(kept) // 5)
    for name, part in (("val", kept[:nval]), ("train", kept[nval:])):
        (out / f"{name}.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in part), encoding="utf-8")
    print(f"kept {len(kept)}/{len(cases)} -> train {len(kept) - nval}, val {nval}")


if __name__ == "__main__":
    main()
