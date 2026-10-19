"""E11 (docs/experiments/e11_prereg.json): LoRA on the 4-bit Qwen3-14B, teaching it gpt-5.4-mini's v4 verdicts.
Runs on the gaming PC in ~/llm/train/.venv (stop the judge first: it holds the GPU).

    python train_lora.py --data sft --out runs/full [--days-share 0.333] [--base google/gemma-4-12B-it-qat-q4_0-unquantized]

--base (E13's Gemma arm, A73): a model that is not already 4-bit is quantised on load the way the Qwen base was (NF4,
double quantisation, bfloat16 compute); everything else is the same recipe. Settings are the pre-registered ones (rank 16, alpha 32, dropout 0.05, all attention and MLP projections; learning rate
1e-4 cosine, warmup 3 %, effective batch 16, 2 epochs, ≤ 1,024 tokens, loss on the answer only); the checkpoint kept is
the one with the lowest validation loss. --days-share trains on the first share of the training days (by date, nested:
the learning curve). Every EVAL_EVERY steps it also scores the validation days per axis: the model's own choice among
"A" / "B" / "na" at each verdict, read from its next-token probabilities (teacher-forced; no generation), against
gpt-5.4-mini's — the curve of how agreement grows on both axes. Metrics go to <out>/metrics.jsonl.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, get_cosine_schedule_with_warmup

BASE = "unsloth/Qwen3-14B-unsloth-bnb-4bit"
RANK, ALPHA, DROPOUT = 16, 32, 0.05
TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
LR, WARMUP, EFFECTIVE_BATCH, EPOCHS, MAX_LEN = 1e-4, 0.03, 16, 2, 1024
EVAL_EVERY = 40                     # optimiser steps between validation scores
AXES = ("econ", "social")
OPTIONS = ("A", "B", "na")


def load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


SEP = "\n\n---\n\n"
SWAP = {"A": "B", "B": "A", "na": "na"}


def swap_example(ex) -> dict | None:
    """E13 (JudgeLM's swap augmentation): the same pair with the articles in the other order and the verdict letters
    swapped with them; None if the article texts contain the separator (the swap would be ambiguous)."""
    user = ex["messages"][1]["content"]
    parts = user.split(SEP)
    if len(parts) != 3 or not parts[0].startswith("Article A\n\n") or not parts[1].startswith("Article B\n\n"):
        return None
    a, b = parts[0][len("Article A\n\n"):], parts[1][len("Article B\n\n"):]
    answer = {ax: SWAP[v] for ax, v in json.loads(ex["messages"][2]["content"]).items()}
    msgs = [ex["messages"][0], {**ex["messages"][1], "content": SEP.join([f"Article A\n\n{b}", f"Article B\n\n{a}", parts[2]])},
            {**ex["messages"][2], "content": json.dumps(answer, separators=(",", ":"))}]
    return {**ex, "messages": msgs, "swapped": True}


def end_of_turn(tok) -> str:
    """What the model's chat template writes after an answer: "<|im_end|>\\n" for Qwen, "<turn|>\\n" for Gemma 4."""
    mark = '{"econ":"na","social":"na"}'
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}, {"role": "assistant", "content": mark}]
    full = tok.apply_chat_template(msgs, tokenize=False, enable_thinking=False)
    return full[full.rindex(mark) + len(mark):]


def encode(tok, ex, end: str) -> dict:
    """Prompt (system + user, thinking off) → labels -100; the answer and end-of-turn (`end`) → labels."""
    prompt = tok.apply_chat_template(ex["messages"][:2], tokenize=False, add_generation_prompt=True, enable_thinking=False)
    answer = ex["messages"][2]["content"] + end
    p = tok(prompt, add_special_tokens=False)["input_ids"]
    a = tok(answer, add_special_tokens=False)["input_ids"]
    ids = (p + a)[:MAX_LEN]
    return {"input_ids": ids, "labels": ([-100] * len(p) + a)[:MAX_LEN], "prompt": p, "answer": json.loads(ex["messages"][2]["content"])}


def slots(tok, prompt: list[int], answer: dict) -> list[tuple[str, str, list[int], int, list[int]]]:
    """Per axis: the context up to the verdict (the true earlier verdicts, teacher-forced) and the token ids that
    begin each option — the model's choice is the option whose first token is most probable there."""
    out = []
    for ax in AXES:
        texts = {o: json.dumps({**answer, ax: o}, separators=(",", ":")) for o in OPTIONS}
        seqs = {o: tok(t, add_special_tokens=False)["input_ids"] for o, t in texts.items()}
        p = 0
        while all(len(s) > p for s in seqs.values()) and len({s[p] for s in seqs.values()}) == 1:
            p += 1
        first = [seqs[o][p] for o in OPTIONS]
        if len(set(first)) == len(OPTIONS):
            out.append((ax, answer[ax], prompt + seqs[OPTIONS[0]][:p], p, first))
    return out


def answer_loss(model, e, weighted: bool = False) -> torch.Tensor:
    """Cross-entropy on the answer tokens only, from logits of the last positions only: the full sequence's logits
    over a 151 k vocabulary would not fit next to a 14B model on 16 GB. weighted: each token by e["weights"] (E13's
    balanced verdict weights), still divided by the number of answer tokens."""
    ids = torch.tensor([e["input_ids"]], device=model.device)
    n = sum(1 for x in e["labels"] if x != -100)
    logits = model(input_ids=ids, logits_to_keep=n + 1).logits[0, :-1].float()
    if not weighted or "weights" not in e:
        return torch.nn.functional.cross_entropy(logits, ids[0, -n:])
    ce = torch.nn.functional.cross_entropy(logits, ids[0, -n:], reduction="none")
    return (ce * torch.tensor(e["weights"], device=ce.device)).sum() / n


def verdict_weights(tok, train: list[dict]) -> dict[str, float]:
    """Per axis, n_na / n_decided over the training answers: decided and "na" answers then carry equal total weight."""
    out = {}
    for ax in AXES:
        dec = sum(e["answer"][ax] != "na" for e in train)
        out[ax] = (len(train) - dec) / max(dec, 1)
    return out


def add_weights(tok, e, w: dict[str, float]) -> dict:
    """The weight of each answer token: w[axis] at a decided verdict's token, 1 elsewhere."""
    n = sum(1 for x in e["labels"] if x != -100)
    weights = [1.0] * n
    for ax, truth, _, p, _ in slots(tok, e["prompt"], e["answer"]):
        if truth != "na" and p < n:
            weights[p] = w[ax]
    return {**e, "weights": weights}


def shared_kv(model) -> list[str]:
    """A73: the k_proj of every attention without a v_proj (Gemma 4's full-attention layers use the keys as values).
    vLLM packs LoRA into q/k/v slices, so a k_proj adapter there would move the keys only when served but keys and
    values in training; these are left out of the LoRA so the served model is the trained one. None in Qwen3."""
    return [f"{n}.k_proj" for n, m in model.named_modules()
            if isinstance(getattr(m, "k_proj", None), torch.nn.Module) and getattr(m, "v_proj", None) is None]


@torch.no_grad()
def score(model, tok, val: list[dict]) -> dict:
    """Validation loss and agreement per axis with gpt-5.4-mini's verdicts."""
    model.eval()
    pairs = {ax: [] for ax in AXES}                                            # (gpt's verdict, the model's)
    for e in val:
        for ax, truth, ctx, _, first in slots(tok, e["prompt"], e["answer"]):
            logits = model(input_ids=torch.tensor([ctx], device=model.device), logits_to_keep=1).logits[0, -1]
            pairs[ax].append((truth, OPTIONS[int(torch.argmax(logits[first]))]))
    losses = [float(answer_loss(model, e)) for e in val]
    model.train()
    out = {"val_loss": sum(losses) / max(len(losses), 1), "val_items": {ax: len(v) for ax, v in pairs.items()}}
    for ax, v in pairs.items():
        n = max(len(v), 1)
        dec = lambda x: x != "na"                                               # noqa: E731
        both = [(t, m) for t, m in v if dec(t) and dec(m)]
        out[f"val_agree_{ax}"] = sum(t == m for t, m in v) / n
        out[f"val_na_{ax}"] = sum(m == "na" for _, m in v) / n                 # how often the model abstains
        out[f"val_flip_{ax}"] = sum(t != m for t, m in both) / max(len(both), 1)   # wrong side, when both decide
        out[f"val_miss_{ax}"] = sum(dec(t) and not dec(m) for t, m in v) / n
        out[f"val_invent_{ax}"] = sum(not dec(t) and dec(m) for t, m in v) / n
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("sft"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--days-share", type=float, default=1.0, help="train on the first share of the training days")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--smoke", type=int, help="only this many training and validation examples (a test of the script)")
    ap.add_argument("--epochs", type=int, default=EPOCHS)
    ap.add_argument("--swap-augment", action="store_true", help="E13: add every training example with A/B swapped")
    ap.add_argument("--decided-weight", choices=["none", "balanced"], default="none",
                    help="E13: weight decided verdict tokens n_na / n_decided per axis")
    ap.add_argument("--base", default=BASE, help="the model to train (A73: not 4-bit yet → quantised on load)")
    args = ap.parse_args()
    torch.manual_seed(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    tok = AutoTokenizer.from_pretrained(args.base)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    end = end_of_turn(tok)
    train = load(args.data / "train.jsonl"); val = load(args.data / "val.jsonl")
    days = sorted({e["day"] for e in train if e.get("day")})
    keep = set(days[: math.ceil(args.days_share * len(days))])
    train = [e for e in train if e.get("day") in keep or not e.get("day")]    # E10's verdicts (no day) in every run
    if args.smoke:
        train, val = train[: args.smoke], val[: args.smoke]
    if args.swap_augment:
        train = train + [x for x in (swap_example(e) for e in train) if x is not None]
    train = [x for x in (encode(tok, e, end) for e in train) if x["labels"][-1] != -100]   # never cut an answer off
    val = [x for x in (encode(tok, e, end) for e in val) if x["labels"][-1] != -100]
    weights = verdict_weights(tok, train) if args.decided_weight == "balanced" else None
    if weights:
        train = [add_weights(tok, e, weights) for e in train]
    meta = {"base": args.base, "train_examples": len(train), "val_examples": len(val), "days_share": args.days_share,
            "train_days": len(keep), "rank": RANK, "alpha": ALPHA, "lr": LR, "epochs": args.epochs,
            "swap_augment": args.swap_augment, "verdict_weights": weights}
    # an explicit quantization_config (even None) overrides a 4-bit checkpoint's own, so pass one only when needed
    quant = {} if getattr(AutoConfig.from_pretrained(args.base), "quantization_config", None) else {"quantization_config": BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)}
    model = AutoModelForCausalLM.from_pretrained(args.base, device_map={"": 0}, dtype=torch.bfloat16, **quant)
    exclude = shared_kv(model)
    meta["lora_excluded"] = exclude
    (args.out / "run.json").write_text(json.dumps(meta, indent=2))
    print(meta, flush=True)
    # not peft's prepare_model_for_kbit_training: it casts the embeddings and the output layer to 32-bit (≈ 6 GB more
    # on a 14B model with a 151 k vocabulary), which a 16 GB card cannot hold; checkpointing alone is enough
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    model.config.use_cache = False
    model = get_peft_model(model, LoraConfig(r=RANK, lora_alpha=ALPHA, lora_dropout=DROPOUT, target_modules=TARGETS,
                                             exclude_modules=exclude or None, task_type="CAUSAL_LM"))
    accum = EFFECTIVE_BATCH                                   # one sequence at a time, accumulated
    steps = math.ceil(len(train) / accum) * args.epochs
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=LR, weight_decay=0.0)
    sched = get_cosine_schedule_with_warmup(opt, max(1, int(WARMUP * steps)), steps)
    log = open(args.out / "metrics.jsonl", "a")

    def write(rec):
        log.write(json.dumps(rec) + "\n"); log.flush(); print(rec, flush=True)

    best, step, t0 = math.inf, 0, time.time()
    write({"step": 0, "examples_seen": 0, **score(model, tok, val)})
    g = torch.Generator().manual_seed(args.seed)
    for epoch in range(args.epochs):
        order = torch.randperm(len(train), generator=g).tolist()
        for k in range(0, len(order), accum):
            group = [train[i] for i in order[k:k + accum]]
            total = 0.0
            for e in group:
                loss = answer_loss(model, e, weighted=True) / len(group)
                loss.backward(); total += float(loss)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(); sched.step(); opt.zero_grad(); step += 1
            if step % EVAL_EVERY == 0 or step == steps:
                rec = {"step": step, "epoch": epoch, "examples_seen": min(step * accum, len(train) * args.epochs), "train_loss": total,
                       "lr": sched.get_last_lr()[0], "minutes": round((time.time() - t0) / 60, 1), **score(model, tok, val)}
                write(rec)
                if rec["val_loss"] < best:
                    best = rec["val_loss"]
                    model.save_pretrained(args.out / "adapter"); tok.save_pretrained(args.out / "adapter")
                    (args.out / "adapter" / "chosen.json").write_text(json.dumps(rec))
    write({"done": True, "best_val_loss": best, "steps": steps, "minutes": round((time.time() - t0) / 60, 1)})


if __name__ == "__main__":
    main()
