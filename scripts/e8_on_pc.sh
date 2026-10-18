#!/bin/sh
# E8 on the gaming PC's GPU (docs/experiments/e8_prereg.json): copy the exported texts over, pause the judge, embed
# them with the three local models, start the judge again, bring the vectors back and import them. Free.
#   PC=<user>@<pc> sh scripts/e8_on_pc.sh            # after: uv run python -m pipeline.experiments.e8 export
# Then: uv run python -m pipeline.experiments.e8 sample → blind labels → analyse.
set -eu
: "${PC:?set PC=<user>@<address of the gaming PC>}"
VECS=data/e8_vectors
REMOTE=llm/e8
ssh "$PC" "mkdir -p ~/$REMOTE"
scp -q infra/pc/embed_texts.py "$PC:$REMOTE/"
for m in "Qwen3-Embedding-0.6B:Qwen/Qwen3-Embedding-0.6B" "bge-m3:BAAI/bge-m3" \
         "e5-large-instruct:intfloat/multilingual-e5-large-instruct"; do
  name=${m%%:*}; hf=${m#*:}
  scp -q "$VECS/texts_$name.jsonl" "$PC:$REMOTE/"
done
ssh "$PC" "systemctl --user stop llm-judge"
trap 'ssh "$PC" "systemctl --user start llm-judge"' EXIT          # the judge comes back even if a run fails
for m in "Qwen3-Embedding-0.6B:Qwen/Qwen3-Embedding-0.6B" "bge-m3:BAAI/bge-m3" \
         "e5-large-instruct:intfloat/multilingual-e5-large-instruct"; do
  name=${m%%:*}; hf=${m#*:}
  ssh "$PC" "cd ~/$REMOTE && ~/llm/.venv/bin/python embed_texts.py --model $hf --input texts_$name.jsonl --output $name.texts.npy"
  scp -q "$PC:$REMOTE/$name.texts.npy" "$VECS/"
  PYTHONPATH=. uv run python -m pipeline.experiments.e8 import --model "$name" --vectors "$VECS/$name.texts.npy"
done
echo "E8 vectors imported. Next: uv run python -m pipeline.experiments.e8 sample"
