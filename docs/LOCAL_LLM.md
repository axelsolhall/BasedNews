# The local judge on the gaming PC (A65)

An OpenAI-compatible LLM server (vLLM) on the gaming PC (RTX 5080, 16 GB, Ubuntu), for judging the history locally
instead of paying for it. Only the home network can reach it, and every request needs the key.

| | |
|---|---|
| address | `http://<pc>:8000/v1` on the home network (the Mac's `.env`: `LOCAL_LLM_BASE_URL`), model name `judge` |
| key | PC: `~/.config/llm/secret.env` (600) · Mac: `LOCAL_LLM_API_KEY` in the git-ignored `.env` |
| model | `infra/pc/llm.env` → `JUDGE_MODEL` (a placeholder until the blind check picks one) |
| service | systemd user service `llm-judge`, restarts itself on a crash |

## Check it (from the Mac)
    uv run python -m pipeline.history.llm_check            # reachable, key enforced, one answer, load test

2026-10-17, Qwen3-14B-AWQ, gpt-5.4-mini-shaped calls (≈ 1,000 tokens in, nothing cached, 174 forced out), 16 in
parallel: 2.85 requests/s, 495 output tokens/s, median 5.6 s → ≈ 10,000 an hour. That shape was wrong for the history
(A65's correction): its calls share the ≈ 400-token instructions (cached) and answer in ≈ 15 tokens with thinking off.
The check now sends replay-shaped calls:

    uv run python -m pipeline.history.llm_check --n 512 --parallel 64 --db data/history/history.sqlite

2026-10-18, the same model, replay-shaped (≈ 580 tokens in, 14 out): 16 in parallel 12.8 calls/s (≈ 46,000 an
hour), 64 in parallel 14.2 calls/s (≈ 51,000 an hour).

## Install or update (on the PC, from a copy of infra/pc)
    sh install.sh             # venv + vLLM (no root), config, key (created once), service

Once, with root (a Mac Terminal, the PC's password): start at boot without logging in

    ssh -t <user>@<pc> 'sudo loginctl enable-linger axel'

## Embeddings on the GPU (E8, the history's vectors)
The judge holds most of the GPU's memory, so stop it while embedding:

    systemctl --user stop llm-judge
    ~/llm/.venv/bin/python embed_texts.py --model BAAI/bge-m3 --input texts.jsonl --output vectors.npy --dtype float16
    systemctl --user start llm-judge

`embed_texts.py` (in `infra/pc/`) reads one JSON line per text, writes the vectors as it goes and resumes where it
stopped. The texts come from the Mac: `pipeline.experiments.e8 export` (E8) or `pipeline.history.vectors export` (the
history); the vectors go back with the matching `import`.

## Everyday
| | |
|---|---|
| logs | `ssh <user>@<pc> journalctl --user -u llm-judge -f` |
| switch the model | edit `JUDGE_MODEL` in `infra/pc/llm.env`, copy it to `~/llm/llm.env`, `systemctl --user restart llm-judge` |
| free the GPU (games, TARS) | `systemctl --user stop llm-judge` (start again with `start`) |

## Why these settings
- vLLM: many requests at once (continuous batching); the history needs throughput, not one fast answer.
- A uv-managed Python: Triton compiles a helper against Python's C headers, which Ubuntu's Python lacks without root.
- `VLLM_USE_FLASHINFER_SAMPLER=0`: FlashInfer's sampler compiles itself with nvcc (not installed); vLLM's own sampler
  does the same without a compiler.
- 0.92 of the GPU, 8 k per request, 4 k prefill chunks: weights 9.4 GB leave 2.5 GB of KV cache = 16,400 tokens ≈ 13
  judge-sized requests at once.
