#!/usr/bin/env bash
# One-command setup for Gremlin. Safe to run more than once, and safe
# to run on a different machine after copying the project over (e.g.
# laptop -> desktop): it skips whatever's already done rather than
# redoing it, and only asks for API keys that aren't already set in
# .env, so a laptop-configured .env carried over to the desktop won't
# get asked for keys a second time.

set -e
cd "$(dirname "$0")"

echo "=== Gremlin setup ==="
echo

# --- 1. Virtual environment ---
if [ ! -d venv ]; then
    echo "[*] Creating virtual environment..."
    python3 -m venv venv
else
    echo "[*] venv already exists, skipping"
fi
source venv/bin/activate

# --- 2. Everything except llama-cpp-python (handled separately below,
#     since which wheel to use depends on this specific machine's hardware) ---
echo "[*] Installing Python dependencies..."
pip install --upgrade pip -q
grep -v "^llama-cpp-python" requirements.txt > /tmp/gremlin-reqs-no-llama.txt
pip install -r /tmp/gremlin-reqs-no-llama.txt -q
rm -f /tmp/gremlin-reqs-no-llama.txt

# --- 3. llama-cpp-python: GPU-aware, falls back to CPU-only automatically ---
echo
echo "[*] Checking for an NVIDIA GPU..."
INSTALLED_LLAMA=false

if command -v nvidia-smi &> /dev/null; then
    # Older drivers print "CUDA Version: X.Y"; newer open-kernel-module
    # drivers (e.g. 610.x) print "CUDA UMD Version: X.Y" instead -- match
    # either so this doesn't silently fall through to CPU-only.
    CUDA_VERSION=$(nvidia-smi 2>/dev/null | grep -oP "CUDA (UMD )?Version: \K[0-9]+\.[0-9]+" | head -1)
    if [ -n "$CUDA_VERSION" ]; then
        CUDA_TAG="cu$(echo "$CUDA_VERSION" | tr -d '.')"
        echo "[+] NVIDIA GPU detected (driver reports CUDA $CUDA_VERSION) -- trying GPU wheel: $CUDA_TAG"
        if pip install llama-cpp-python --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/$CUDA_TAG" --force-reinstall -q 2>/dev/null; then
            echo "[+] GPU-accelerated llama-cpp-python installed ($CUDA_TAG)"
            INSTALLED_LLAMA=true
        elif command -v nvcc &> /dev/null; then
            # No prebuilt wheel for this CUDA version yet (common right
            # after a new CUDA release) -- build against the local toolkit
            # instead of giving up and going CPU-only.
            echo "[!] No matching prebuilt wheel for $CUDA_TAG -- building from source against local CUDA toolkit instead"
            if CMAKE_ARGS="-DGGML_CUDA=on" pip install --force-reinstall --no-cache-dir llama-cpp-python -q; then
                echo "[+] GPU-accelerated llama-cpp-python built and installed (source, CUDA $CUDA_VERSION)"
                INSTALLED_LLAMA=true
            else
                echo "[!] Source build failed -- falling back to CPU-only"
            fi
        else
            echo "[!] No matching prebuilt wheel for $CUDA_TAG and no local nvcc to build against -- falling back to CPU-only"
        fi
    else
        echo "[!] nvidia-smi found but couldn't read a CUDA version from it -- falling back to CPU-only"
    fi
else
    echo "[*] No NVIDIA GPU detected on this machine -- installing CPU-only (fine for a laptop without a discrete GPU)"
fi

if [ "$INSTALLED_LLAMA" = false ]; then
    pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu -q
    echo "[+] CPU-only llama-cpp-python installed"
fi

# --- 4. .env / API keys -- only asks for what isn't already set ---
echo
if [ ! -f .env ]; then
    cp .env.example .env
fi

has_real_value() {
    # true if the var is set to something that isn't the .env.example placeholder
    grep -q "^$1=.\+" .env 2>/dev/null && ! grep -q "^$1=.*your-.*-here" .env 2>/dev/null
}

if has_real_value "ANTHROPIC_API_KEY"; then
    echo "[*] ANTHROPIC_API_KEY already set in .env, leaving it as-is"
else
    read -rp "Enter your Anthropic API key (blank to skip): " ANTHROPIC_KEY
    if [ -n "$ANTHROPIC_KEY" ]; then
        sed -i "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$ANTHROPIC_KEY|" .env
        echo "[+] Saved"
    fi
fi

if has_real_value "GEMINI_API_KEY"; then
    echo "[*] GEMINI_API_KEY already set in .env, leaving it as-is"
else
    read -rp "Enter your Gemini API key (blank to skip): " GEMINI_KEY
    if [ -n "$GEMINI_KEY" ]; then
        sed -i "s|^GEMINI_API_KEY=.*|GEMINI_API_KEY=$GEMINI_KEY|" .env
        echo "[+] Saved"
    fi
fi

echo
echo "=== Setup complete ==="
echo "Try it: source venv/bin/activate && python main.py list"
echo "(or chmod +x gremlin && ./gremlin list -- see README for the one-time PATH setup)"
