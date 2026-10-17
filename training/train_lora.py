"""16-bit LoRA of gemma-4-12B-it on explainer targets. Loss only on the answer.
python training/train_lora.py --train data/train.jsonl --val data/val.jsonl --out runs/ft1 --epochs 1
After a crash: the same command + --resume continues from <out>/ckpt (saved every --save-every steps)."""
import argparse, json, math, os, pathlib, random, sys, time
# On the shared DGX the PyTorch cache grew to 111 GB with only ~40 GB really used; expandable segments keep it close to real use.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForImageTextToText, AutoProcessor
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lre.checks import SYSTEM_PROMPT

BASE = str(pathlib.Path.home() / "gajanand/models/gemma-4-12B-it")
ap = argparse.ArgumentParser()
ap.add_argument("--train", required=True); ap.add_argument("--val"); ap.add_argument("--out", required=True)
ap.add_argument("--epochs", type=float, default=1); ap.add_argument("--max-steps", type=int, default=0)
ap.add_argument("--lr", type=float, default=1e-4); ap.add_argument("--rank", type=int, default=16)
ap.add_argument("--accum", type=int, default=8); ap.add_argument("--eval-every", type=int, default=50)
ap.add_argument("--save-every", type=int, default=25)
ap.add_argument("--resume", action="store_true", help="continue from <out>/ckpt after a crash (same command + --resume)")
args = ap.parse_args()
out = pathlib.Path(args.out); out.mkdir(parents=True, exist_ok=True)
log = open(out / "train.log", "a")
def say(*a):
    s = " ".join(map(str, a)); print(s, flush=True); log.write(s + "\n"); log.flush()

proc = AutoProcessor.from_pretrained(BASE)
tok = proc.tokenizer
model = AutoModelForImageTextToText.from_pretrained(BASE, dtype=torch.bfloat16, device_map="cuda")
model.config.use_cache = False


def encode(ex):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(ex["input"], ensure_ascii=False)}]
    prompt = proc.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False, enable_thinking=False)
    full = prompt + ex["target"] + "<turn|>\n"
    ids = tok(full, return_tensors="pt", add_special_tokens=False)["input_ids"]
    n_prompt = tok(prompt, return_tensors="pt", add_special_tokens=False)["input_ids"].shape[1]
    labels = ids.clone(); labels[:, :n_prompt] = -100
    return {"input_ids": ids.cuda(), "attention_mask": torch.ones_like(ids).cuda(), "labels": labels.cuda()}


load = lambda f: [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()] if f else []
train, val = load(args.train), load(args.val)[:100]
LORA_RE = r".*language_model.*\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)$"
model = get_peft_model(model, LoraConfig(r=args.rank, lora_alpha=args.rank, lora_dropout=0.05,
                                         target_modules=LORA_RE, task_type="CAUSAL_LM"))
model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
model.enable_input_require_grads()
steps = args.max_steps or math.ceil(len(train) * args.epochs / args.accum)
opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr, weight_decay=0.0)
warm = max(1, steps // 20)
sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1, (s + 1) / warm) * 0.5 * (1 + math.cos(math.pi * min(s, steps) / steps)))


@torch.no_grad()
def val_loss():
    model.eval(); tot = sum(model(**encode(ex)).loss.item() for ex in val); model.train()
    torch.cuda.empty_cache()  # give the validation pass's cache back to the shared box
    return tot / max(len(val), 1)


say(f"train {len(train)} val {len(val)} steps {steps} accum {args.accum} lr {args.lr} rank {args.rank}")
rng, order, step, micro, t0, run, best = random.Random(42), [], 0, 0, time.time(), 0.0, float("inf")
CKPT = out / "ckpt"  # checkpoint every --save-every steps: adapter + optimizer + scheduler + data order


def save_ckpt():
    model.save_pretrained(CKPT)
    torch.save({"opt": opt.state_dict(), "sched": sched.state_dict(), "step": step, "best": best,
                "order": order, "rng": rng.getstate()}, CKPT / "trainer_state.pt")


if args.resume and (CKPT / "trainer_state.pt").exists():
    from peft import set_peft_model_state_dict
    from safetensors.torch import load_file
    set_peft_model_state_dict(model, load_file(str(CKPT / "adapter_model.safetensors")))
    st = torch.load(CKPT / "trainer_state.pt", weights_only=False)
    opt.load_state_dict(st["opt"]); sched.load_state_dict(st["sched"])
    step, best, order = st["step"], st["best"], st["order"]; rng.setstate(st["rng"])
    say(f"RESUMED from step {step} (best val_loss {best:.4f})")
elif val:
    say(f"step 0 val_loss {val_loss():.4f}")
step0 = step
model.train()
while step < steps:
    if not order:
        order = list(range(len(train))); rng.shuffle(order)
    loss = model(**encode(train[order.pop()])).loss / args.accum
    loss.backward(); run += loss.item(); micro += 1
    if micro % args.accum == 0:
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step(); sched.step(); opt.zero_grad(set_to_none=True); step += 1
        if step % 5 == 0:
            per = (time.time() - t0) / max(step - step0, 1)
            say(f"step {step}/{steps} loss {run / 5:.4f} {per:.1f}s/step "
                f"eta {(steps - step) * per / 60:.0f}min mem {torch.cuda.max_memory_allocated() / 2**30:.1f}G")
            run = 0.0
        if val and (step % args.eval_every == 0 or step == steps):
            v = val_loss(); say(f"step {step} val_loss {v:.4f}")
            if v < best:
                best = v; model.save_pretrained(out / "adapter_best")
        if step % args.save_every == 0:  # includes the final step, so a resume test of N steps resumes from N
            save_ckpt(); say(f"checkpoint saved at step {step}")
model.save_pretrained(out / "adapter_last")
say("TRAINED")
