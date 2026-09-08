#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker Desktop on macOS or Docker Engine on Linux, then rerun this script." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but its daemon is not available. Start Docker and rerun this script." >&2
  exit 1
fi

"$(cd "$(dirname "$0")" && pwd -P)/build_image.sh"
