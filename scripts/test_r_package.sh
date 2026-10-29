#!/usr/bin/env bash
# Test the R package r/sdgr in a rocker/r-ver container with Podman.
#
# The container mounts the repository and the uv binary of the host. uv makes
# the sdg environment, its Python and its cache in sdg_output/r_check/, so the
# .venv of the host stays as it is. The R script writes the help files with
# roxygen2, then runs R CMD build and R CMD check with the tests.
#
# Usage: scripts/test_r_package.sh
set -euo pipefail

image=docker.io/rocker/r-ver:4.5.1
work=sdg_output/r_check
uv_binary=$(command -v uv)

mkdir -p "$work"
podman run --rm -v "$PWD:/work" -v "$uv_binary:/usr/local/bin/uv:ro" -w /work \
  -e UV_PROJECT_ENVIRONMENT="/work/$work/venv" \
  -e UV_CACHE_DIR="/work/$work/uv_cache" \
  -e UV_PYTHON_INSTALL_DIR="/work/$work/python" \
  -e SDGR_COMMAND="uv run --quiet --frozen --project /work sdg" \
  "$image" Rscript scripts/check_r_package.R "$work/r_lib" "$work/check"
