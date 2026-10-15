# Report Saathi: understand your lab report

**Report Saathi** (*saathi* means "companion") explains a blood-test report in simple English. You upload the PDF your lab sent on WhatsApp, or a photo of the paper report, and say whether you're pregnant. A few seconds later you see:

- which values are **worth asking your doctor about**, and what each test measures
- **small differences** that usually don't matter on their own, grouped in one line
- your **normal** results in one line
- the lab's own comments, quoted as the lab's words
- **neutral questions to ask your doctor**
- an urgent "contact your doctor today" line, **only** for critical values

It **never diagnoses** and **never suggests medicines, supplements or doses**. Every answer ends with: *"This explains your report. It is not a diagnosis. Please show this report to your doctor."*

> Status: work in progress for the DEV "Hacktoberfest Open-Source AI Challenge: Side Hustle" (October 2026). The rules engine, the safety checks and the training pipeline are in this repo. The web app and the worker are coming next. See [Roadmap](#roadmap).

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
3. **Explainer (Gemma 4 12B, fine-tuned).** It turns the flagged rows into the explanation above. **No AI decides whether a value is abnormal**; the model only explains decisions the rules already made.

## Why open models

- **Privacy.** Lab reports carry names, conditions and pregnancies. Here, every model runs on a DGX Spark our team owns. No report is ever sent to an AI company. The web app on Render only holds the uploaded file until the worker picks it up, and deletes the whole job within 24 hours.
- **Cost.** There's no per-report API bill, so a small monthly price per pharmacy can still cover running costs.
- **A niche the big models get wrong.** We can teach the model *our* rules: Indian lab formats, trimester ranges, never diagnosing, and grouping minor values. The fine-tune is a 16-bit LoRA of `google/gemma-4-12B-it`, taught by Gemma 4 31B on thousands of synthetic reports. The goal is a small, fast model that explains as well as the big one.

## Training (how the explainer is fine-tuned)

1. [`training/make_cases.py`](training/make_cases.py) generates synthetic reports in real lab formats (40% pregnant, plus edge cases: critical values, band gaps, unit typos, unreadable values, unknown tests) and runs them through the rules engine.
2. [`training/teacher.py`](training/teacher.py): Gemma 4 31B writes an explanation for each one. **An example is kept only if it passes every automatic safety check** ([`lre/checks.py`](lre/checks.py): no diagnosis, no medicine words, every flagged value mentioned, urgent line only when critical, disclaimer present, length limit) **and a strict judge review.**
3. LoRA in bf16 on the 12B, then evaluated on a separate 32-case test set against the base 12B and the 31B. It ships only if it beats the base model with no new safety failures.

**Real lab reports are never used for training.** A handful of anonymised real reports are used only for testing, and they aren't in this repo.

## Repo layout

```
lre/ranges.py        parse printed reference ranges and their labels
lre/rules.csv        the rule table (doctor-reviewable)
lre/rules.py         raw rows + patient -> flagged rows
lre/checks.py        automatic safety and quality checks on an explanation
lre/llm.py           tiny client for any OpenAI-compatible server (vLLM)
lre/explainer_prompt.md  the explainer's system prompt
training/            synthetic cases, teacher filtering
tests/               pytest
```

## Run the tests

```bash
pip install -r requirements-worker.txt
python -m pytest -q
```

## Roadmap

- [x] Rules table and engine, safety checks, synthetic data and teacher pipeline
- [ ] Fine-tune the explainer (16-bit LoRA, Gemma 4 12B) and publish the evaluation
- [ ] Reader (Gemma 4 vision + PDF text check)
- [ ] Web app and job queue on Render, with a one-click `render.yaml` blueprint
- [ ] Worker on the DGX, streaming explanations
- [ ] Review of the pregnancy ranges and critical limits by a gynaecologist

## Disclaimer

Report Saathi gives general information to help you talk to your doctor. It is not medical advice, not a diagnosis, and not a substitute for a doctor. Reference ranges in `rules.csv`, especially the pregnancy ranges, are drafts until the medical review is complete.

## License

MIT © 2026 Gajanand V Dhayagode
