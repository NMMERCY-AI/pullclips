#!/usr/bin/env bash

set -e

echo "========================================"
echo "Installing Python dependencies"
echo "========================================"

pip install -r requirements.txt

echo "========================================"
echo "Installing Deno"
echo "========================================"

export DENO_INSTALL="$PWD/.deno"

curl -fsSL https://deno.land/install.sh | sh

echo "========================================"
echo "Checking Deno"
echo "========================================"

if [ -x "$PWD/.deno/bin/deno" ]; then
    echo "✅ Deno installed successfully"
    "$PWD/.deno/bin/deno" --version
else
    echo "❌ Deno installation failed"
    exit 1
fi

echo "========================================"
echo "Build complete"
echo "========================================"