#!/bin/sh
# Set up (or update) the local judge on the gaming PC, without root (A65). Run on the PC from this folder:
#   sh install.sh
# Root is needed once, separately, for start-at-boot and the firewall (docs/LOCAL_LLM.md).
set -e
mkdir -p ~/llm ~/.config/llm ~/.config/systemd/user
[ -x ~/llm/.venv/bin/vllm ] || {
  ~/miniconda3/bin/python -m pip install -q uv
  # a uv-managed Python: it ships its C headers, which Triton compiles against (Ubuntu's needs python3-dev, i.e. root)
  ~/miniconda3/bin/uv venv -q --python-preference only-managed --python 3.12 ~/llm/.venv
  ~/miniconda3/bin/uv pip install -q --python ~/llm/.venv/bin/python vllm
}
~/miniconda3/bin/uv pip install -q --python ~/llm/.venv/bin/python sentence-transformers   # embed_texts.py (history vectors)
cp llm.env ~/llm/llm.env
cp llm-judge.service ~/.config/systemd/user/llm-judge.service
[ -s ~/.config/llm/secret.env ] || { umask 077; printf 'VLLM_API_KEY=%s\n' "$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')" > ~/.config/llm/secret.env; }
chmod 600 ~/.config/llm/secret.env
systemctl --user daemon-reload
systemctl --user enable --now llm-judge
echo "llm-judge started; first start downloads the model (several GB). Logs: journalctl --user -u llm-judge -f"
