# Report Saathi: understand your lab report

**Report Saathi** (*saathi* means "companion") explains a blood-test report in simple English. You upload the PDF your lab sent on WhatsApp, or a photo of the paper report, and say whether you're pregnant. A few seconds later you see:

- which values are **worth asking your doctor about**, and what each test measures
- **small differences** that usually don't matter on their own, grouped in one line
- your **normal** results in one line
- the lab's own comments, quoted as the lab's words
- **neutral questions to ask your doctor**
- an urgent "contact your doctor today" line, **only** for critical values

It **never diagnoses** and **never suggests medicines, supplements or doses**. Every answer ends with: *"This explains your report. It is not a diagnosis. Please show this report to your doctor."*

> Built for the DEV "Hacktoberfest Open-Source AI Challenge: Side Hustle" (15–19 October 2026). Measured results and known problems are in [Results](#results); changes made after the submission deadline are listed in [After the deadline](#after-the-deadline).

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/gajanand27-05/report-saathi)

## Who it's for

- **Pharmacies.** Customers often hand the pharmacist a report and ask "is this bad?". The pharmacist puts a QR code on the counter, and customers scan it and upload their report. Usage is counted per shop (`?shop=<name>`), so a pharmacy pays a small monthly fee.
- **Pregnant women.** Pregnancy changes what's "normal". Haemoglobin, thyroid (TSH, T3, T4) and the sugar test (OGTT) all have different ranges in each trimester. A generic explainer that uses adult ranges gets these wrong. Report Saathi picks the trimester range, and says so.

## How it works

```
phone ──upload──► web app on Render ◄── polls (outbound only) ── worker on our own GPU box (DGX Spark)
                  job queue (Postgres)                            1. READER     Gemma 4 reads the report into rows
                  deletes everything after 24 h                   2. RULES      rules.csv + rules.py flag each value (no AI)
   ◄── explanation streams back ───┘                              3. EXPLAINER  fine-tuned Gemma 4 12B writes the explanation
```

1. **Reader (Gemma 4, vision).** Most Indian lab PDFs have a text layer. Gemma gets the page image plus the exact text, and returns each test, value, unit and printed range. **Every value it returns must appear in the PDF's text**; anything else is marked "couldn't read". For photos, the app shows the rows it read and asks "Does this match your report?" before explaining anything.
2. **Rules (plain Python, no AI).** [`lre/rules.csv`](lre/rules.csv) is a table a doctor can review line by line: test names as labs print them, units, ranges by sex, pregnancy ranges by trimester, critical limits, possible limits and importance. [`lre/rules.py`](lre/rules.py) decides High / Low / Borderline / Critical:
   - The **lab's printed range comes first**, because each lab's machines differ. When the lab prints several ranges (child/adult, 1st/2nd/3rd trimester, pregnant/non-pregnant), it picks the one that matches the patient.
   - It understands **bands** ("5.7–6.4 = Pre-diabetic", "200–239 = Borderline") and values that fall **between** bands.
   - It catches **printing mistakes**. We found a real lab printing platelet counts like "250" with the unit "**lakhs**/cmm", which would mean 100× too many. Report Saathi reads it as 2.5 lakh/cmm and says why, instead of raising an alarm.
   - **Small deviations are grouped** (importance 2), so a full health-checkup report shows a handful of things worth asking about instead of sixteen alarms.
   - **Pregnancy only comes from the user.** The app asks; it never guesses.
3. **Explainer (Gemma 4).** It turns the flagged rows into the explanation above. We fine-tuned Gemma 4 12B for this and compared it with Gemma 4 31B; [Results](#results) says which one serves the app and why. **No AI decides whether a value is abnormal**; the model only explains decisions the rules already made.

## Why open models

- **Privacy.** Lab reports carry names, conditions and pregnancies. Here, every model runs on a DGX Spark our team owns. No report is ever sent to an AI company. The web app on Render only holds the uploaded file until the worker picks it up, and deletes the whole job within 24 hours.
- **Cost.** There's no per-report API bill, so a small monthly price per pharmacy can still cover running costs.
- **A niche the big models get wrong.** We can teach the model *our* rules: Indian lab formats, trimester ranges, never diagnosing, and grouping minor values. We fine-tuned `google/gemma-4-12B-it` (16-bit LoRA), taught by Gemma 4 31B on thousands of synthetic reports, and measured what it bought us, including where it got worse.

## Training (how the explainer is fine-tuned)

1. [`training/make_cases.py`](training/make_cases.py) generates synthetic reports in real lab formats (40% pregnant, plus edge cases: critical values, band gaps, unit typos, unreadable values, unknown tests) and runs them through the rules engine.
2. [`training/teacher.py`](training/teacher.py): Gemma 4 31B writes an explanation for each one. **An example is kept only if it passes every automatic safety check** ([`lre/checks.py`](lre/checks.py): no diagnosis, no medicine words, every flagged value mentioned, urgent line only when critical, disclaimer present, length limit) **and a strict judge review.**
3. LoRA in bf16 on the 12B, then evaluated on a separate 36-case test set (8 from real reports, 28 synthetic) against the base 12B and the 31B, and read blind side by side against the 31B. See [Results](#results).

**Real lab reports are never used for training.** A handful of anonymised real reports are used only for testing, and they aren't in this repo.

## Results

Measured on the team's DGX Spark, 17–18 October 2026. The raw outputs are in a private repo (they contain numbers from real reports).

**Reader** (27 real reports with names removed, 969 rows; used only for testing):

| | Gemma 4 31B | Gemma 4 12B (live) |
|---|---|---|
| High/low flags the user sees | **960 / 960** | 958 / 960 (one low HDL read as normal) |
| Values exactly right | 963 / 969 | 966 / 969 |
| Values it made up | 1, caught by the PDF-text check | 0 |
| Seconds per report (median) | **37** | 110 |

The live app runs **one Gemma 4 12B for both jobs**: its own weights read the report and our LoRA (same vLLM server) writes the explanation. The 31B reads better and faster, but it was only ever the teacher, and one model leaves the shared GPU box room for other work. The cost: one low HDL read as normal (a misread printed range), and 3–5 minutes for a full check-up (about one minute for a short report).

**Explainer.** Test set: 36 cases × 2 runs = 72 answers. "Passes" = clears the automatic checks *and* a Gemma 4 31B judge.

| Model | Passes (of 72) | Rule breaks | s / explanation |
|---|---|---|---|
| 12B base | 39 | 6 | 17 |
| 31B base | 53 | 9 | 4 |
| 12B fine-tune 1 | 59 | 6 | 17 |
| 12B fine-tune 2 | **63** | **0** | 17 |
| 12B fine-tune 2, FP8 + MTP drafter | 59 | 3 | 3.4 |

That table **does not mean the 12B explains better.** In a blind side-by-side review of 13 reports (fine-tune 2 against the 31B, shuffled), **the 31B was preferred 9 times, the fine-tune 3 times, 1 tie.** Why the two disagree:

- The fine-tune was trained on 31B answers that passed **the same checks and the same judge**, so it learned to pass them.
- To stop vague answers ("this can happen for various reasons"), prompt v1.2 asked for "concrete, everyday, non-alarming reasons" and gave examples, including *the time of day* and *how the sample was handled*. Both models then blamed those for almost everything, even HbA1c (a 3-month average) and a critically low haemoglobin. The fine-tune learned it hardest: in the 72 answers it blamed the time of day 30 times and sample handling 27 times; the 31B 16 and 18. The judge rewarded it, because its own instructions listed the same examples.

Changed in prompt v1.4 / judge v3 (`lre/explainer_prompt.md`, `lre/checks.py`), **not yet re-tested on the DGX**: a reason must be a real, common cause for *that* test, or none; no reassuring reasons for critical values or positive screening tests; the urgent line comes straight after the overview; units always shown; no invented "all other tests are normal" or lab notes.

**Known problems**
- A rules bug, now fixed: the lab prints platelets as "373 lakhs/cmm" (it means 373 thousand). The engine converted that to 3.73 lakh but the note said "read as thousand/cmm", which would read as 3,730. The note now gives both numbers.
- Counting on very large reports (a 70-value check-up) is sometimes off.
- No doctor has reviewed `rules.csv` yet. All real test reports so far come from one lab's template.
- The synthetic training data's calculated rows (ratios, eAG) are random rather than computed from their inputs.

## After the deadline

The submission deadline was 18 October 2026, 11:59 PM PDT (19 October, 12:29 PM IST). Commits after it are listed here.

- *(none yet)*

## Repo layout

```
lre/ranges.py        parse printed reference ranges and their labels
lre/rules.csv        the rule table (doctor-reviewable)
lre/rules.py         raw rows + patient -> flagged rows
lre/checks.py        automatic safety and quality checks on an explanation
lre/llm.py           tiny client for any OpenAI-compatible server (vLLM)
lre/explainer_prompt.md  the explainer's system prompt
lre/pdftext.py       PDF text lines, page images, age/sex from the header
lre/reader.py        Gemma reads a page into rows; values checked against the PDF text
lre/explainer.py     streams an explanation from the fine-tuned model
app/                 FastAPI web app for Render: upload, result page, job queue, worker API, 24-hour cleanup
worker/worker.py     runs on the DGX: polls the app, reader -> rules -> explainer, streams text back
training/            synthetic cases, teacher filtering, 16-bit LoRA training
render.yaml          Render blueprint: web service + Postgres (free plan)
tests/               pytest
```

## Run it yourself

- **Web app:** deploy the blueprint (button above). It creates the web service and a Postgres database on Render's **free plan** (no card needed) and generates `WORKER_TOKEN`. Jobs older than 24 hours are deleted by the app itself whenever the worker checks in. For an always-on paid setup, change the plans in `render.yaml`.
- **Worker** (any machine with an OpenAI-compatible server such as vLLM serving Gemma 4):
  ```bash
  pip install -r requirements-worker.txt
  APP_URL=https://<your-app>.onrender.com WORKER_TOKEN=<token>   READER_URL=http://127.0.0.1:8301/v1 READER_MODEL=<gemma-4 vision model>   EXPLAINER_URL=http://127.0.0.1:8300/v1 EXPLAINER_MODEL=<explainer model>   python worker/worker.py
  ```
  The worker only makes outbound HTTPS calls, so the GPU machine needs no open ports.

## Run the tests

```bash
pip install -r requirements-worker.txt
python -m pytest -q
```

## Roadmap

- [x] Rules table and engine, safety checks, synthetic data and teacher pipeline
- [x] Fine-tune the explainer (16-bit LoRA, Gemma 4 12B) and publish the evaluation ([Results](#results))
- [x] Reader (Gemma 4 vision + PDF text check), web app + job queue, DGX worker (tested end to end locally)
- [x] Deployed on Render (`render.yaml` blueprint, free plan) with the worker on the DGX
- [x] Reader accuracy on real reports published
- [ ] Re-run the explainer test with prompt v1.4 / judge v3, then a second blind review
- [ ] Retrain the fine-tune on v1.4 data
- [ ] Review of the pregnancy ranges and critical limits by a gynaecologist

## Disclaimer

Report Saathi gives general information to help you talk to your doctor. It is not medical advice, not a diagnosis, and not a substitute for a doctor. Reference ranges in `rules.csv`, especially the pregnancy ranges, are drafts until the medical review is complete.

## License

MIT © 2026 Gajanand V Dhayagode
