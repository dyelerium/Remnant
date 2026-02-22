#!/usr/bin/env bash
# Remnant one-shot installer
# Usage: bash install.sh [--dev]
set -euo pipefail

REMNANT_DIR="${REMNANT_DIR:-/opt/remnant}"
VENV_DIR="$REMNANT_DIR/.venv"
DEV_MODE="${1:-}"

echo "=== Remnant Framework Installer ==="

# Detect OS
if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    OS=$ID
else
    OS="unknown"
fi

# Install system deps
if [[ "$OS" == "ubuntu" || "$OS" == "debian" ]]; then
    apt-get update -q
    apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3-pip \
        docker.io docker-compose-plugin \
        curl git build-essential
elif [[ "$OS" == "fedora" || "$OS" == "rhel" || "$OS" == "centos" ]]; then
    dnf install -y python3.11 python3-pip docker docker-compose git curl
fi

# Create install directory
mkdir -p "$REMNANT_DIR"
cd "$REMNANT_DIR"

# Copy source if run from repo
if [[ -f "$(dirname "$0")/requirements.txt" ]]; then
    cp -r "$(dirname "$0")/." "$REMNANT_DIR/"
fi

# Create Python venv
python3.11 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

# Install Python deps
pip install --upgrade pip
pip install -r requirements.txt

# Create required directories
mkdir -p memory/projects logs workspace /tmp/remnant

# Create .env if missing
if [[ ! -f .env ]]; then
    cp .env.example .env
    echo ""
    echo "⚠️  Created .env from .env.example"
    echo "   Edit $REMNANT_DIR/.env and set your API keys before starting."
fi

# Install systemd service (if systemd available and not dev mode)
if [[ "$DEV_MODE" != "--dev" ]] && command -v systemctl &>/dev/null; then
    cp remnant.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable remnant
    echo "✓ Systemd service installed: remnant.service"
fi

echo ""
echo "=== Installation complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit $REMNANT_DIR/.env with your API keys"
echo "  2. docker compose up -d          (production)"
echo "     OR: source .venv/bin/activate && uvicorn api.main:app (dev)"
echo ""
echo "Optional profiles:"
echo "  docker compose --profile whatsapp up -d   (WhatsApp QR bridge)"
echo "  docker compose --profile ollama up -d     (local Ollama LLM)"
