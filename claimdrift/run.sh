#!/usr/bin/env bash
# Run a claimdrift command inside the WSL uv env (Gemini key from .env). Targets the cloud system by default;
# prefix CLAIMDRIFT_LOCAL=1 for the local stack (docker ES, stdio MCP, in-process agents).
#   wsl -d Ubuntu-22.04 -- bash /home/riku_miku/claim_drift/claimdrift/run.sh analyze incubation_travellers_eurosurv
#   wsl -d Ubuntu-22.04 -- bash /home/riku_miku/claim_drift/claimdrift/run.sh test
# Long jobs: setsid nohup bash claimdrift/run.sh eval-drift > /tmp/x.log 2>&1 & disown; sleep 30
cd /home/riku_miku/claim_drift || exit 1
export PATH="$HOME/.local/bin:$PATH"
export PYTHONIOENCODING=utf-8
if [ "$1" = "test" ]; then
  shift
  exec uv run python -m unittest discover -s claimdrift/tests -t . -v "$@"
fi
exec uv run python -m claimdrift "$@"
