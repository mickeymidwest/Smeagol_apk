#!/usr/bin/env bash
# One command to go from a bare machine to a running Gremlin.
#
# Usage:
#   bash bootstrap.sh <your-github-repo-url>
# or, if you've already cloned the repo and are running this from
# inside it (or a directory containing it):
#   bash bootstrap.sh

set -e

REPO_URL="${1:-}"
TARGET_DIR="gremlin"

echo "=== Gremlin bootstrap ==="
echo

if [ -f "setup.sh" ] && [ -f "main.py" ]; then
    # Already running from inside an existing clone
    TARGET_DIR="."
    echo "[*] Already inside a Gremlin checkout, using it directly"
elif [ -d "$TARGET_DIR" ] && [ -f "$TARGET_DIR/setup.sh" ]; then
    echo "[*] $TARGET_DIR/ already exists locally, using it directly"
elif [ -n "$REPO_URL" ]; then
    echo "[*] Cloning $REPO_URL..."
    git clone "$REPO_URL" "$TARGET_DIR"
else
    echo "Usage: bash bootstrap.sh <your-github-repo-url>"
    echo "(or run this from inside an already-cloned gremlin/ directory)"
    exit 1
fi

cd "$TARGET_DIR"
chmod +x setup.sh
[ -f gremlin ] && chmod +x gremlin

echo
echo "[*] Running setup (venv, dependencies, GPU detection, API keys)..."
echo
./setup.sh

echo
echo "=== Base setup complete ==="
echo
echo "config/models.yaml currently expects 5 local models. Each of these"
echo "is an interactive search -- you pick the exact repo and"
echo "quantization yourself, since auto-picking the first search result"
echo "risks silently grabbing the wrong file. Confirmed exact sources"
echo "(see README's \"Confirmed model sources\" section for quant notes,"
echo "especially the two -- qwythos-9b and gpt-oss-20b -- that do NOT"
echo "have a Q4_K_M and need a specific smaller quant picked instead"
echo "on an 8GB card):"
echo
echo "  source venv/bin/activate"
echo '  python main.py models --hf "Huihui-Qwythos-9B-Claude-Mythos-5-1M-abliterated-GGUF"  # -> qwythos-9b (primary) -- pick Q6_K, tightest fit on 8GB'
echo '  python main.py models --hf "OpenAi-GPT-oss-20b-abliterated-uncensored-NEO-Imatrix-gguf"  # -> gpt-oss-20b -- pick IQ4_NL'
echo '  python main.py models --hf "gemma-3-12b-it-abliterated"     # -> gemma-3-12b -- compare mlabonne vs huihui-ai sources'
echo '  python main.py models --hf "Huihui-Qwen3-Coder-30B-A3B-Instruct-abliterated-GGUF"  # -> qwen3-coder --'
echo '                                                                  # mradermacher'"'"'s GGUF/i1-GGUF repo;'
echo '                                                                  # MUCH bigger than the other 4 (~16GB+'
echo '                                                                  # even at the smallest 4-bit quant) --'
echo '                                                                  # needs heavy CPU/RAM offload on 8GB'
echo '  python main.py models --hf "DeepSeek-R1-Distill-Qwen-7B-abliterated"  # -> deepseek-r1-distill-8b -- no exact'
echo '                                                                  # "Qwen-8B" distill exists; this 7B one or'
echo '                                                                  # DeepSeek-R1-0528-Qwen3-8B-abliterated are'
echo '                                                                  # the closest real options, your call'
echo
echo "After each download, update the matching model_path placeholder"
echo "in config/models.yaml if it didn't register under the exact name"
echo "shown above (check with: python main.py list)."
echo
echo "Once those are in place: python main.py chat gremlin"
echo
echo "Optional next steps, not run automatically (both need your sudo"
echo "password and a bit of manual editing -- see README for exact steps):"
echo "  - Auto-start on boot:        deploy/gremlin.service"
echo "  - Remote admin/reboot setup: gremlin admin-token"
