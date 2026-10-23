#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

UV_IMAGE=ghcr.io/astral-sh/uv:0.6.0-python3.12-bookworm-slim

for project in services/*/pyproject.toml; do
  dir="$(dirname "$project")"
  echo "== uv lock $dir"
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp \
    --env UV_CACHE_DIR=/tmp/uv-cache \
    --volume "$PWD/$dir:/project" \
    --workdir /project \
    "$UV_IMAGE" uv lock
done
