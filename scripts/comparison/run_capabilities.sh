#!/usr/bin/env bash
# Run the capability checks of capabilities.txt on sdg, FakeDataR and Faker.
#
# The script writes the invented input, runs the checks of each tool and
# writes one table. FakeDataR runs in a rocker/r-ver container with Podman, and
# Faker runs in its own uv environment. The R package sdgr runs in the same
# container image with the uv of the host. The outputs go to
# sdg_output/comparison/capabilities/run_<timestamp>/.
#
# Usage: scripts/comparison/run_capabilities.sh
set -euo pipefail

base=sdg_output/comparison/capabilities
run_dir=$base/run_$(date +%Y_%m_%d_%H%M%S)
input=$base/input
image=docker.io/rocker/r-ver:4.5.1
source_dir=sdg_output/comparison/fakedatar_src
tarball=$source_dir/FakeDataR_0.2.2.tar.gz
library_dir=sdg_output/comparison/r_lib

mkdir -p "$run_dir" "$source_dir" "$library_dir"
uv run python scripts/comparison/make_capability_input.py "$input"
uv run python scripts/comparison/check_sdg.py "$input" "$run_dir"
uv run scripts/comparison/check_faker.py "$input" "$run_dir"

if [[ ! -f $tarball ]]; then
  curl -sSfL -o "$tarball" https://cran.r-project.org/src/contrib/Archive/FakeDataR/FakeDataR_0.2.2.tar.gz \
    || curl -sSfL -o "$tarball" https://cran.r-project.org/src/contrib/FakeDataR_0.2.2.tar.gz
fi
echo "$(cat scripts/comparison/fakedatar_0.2.2.sha256)  $tarball" | sha256sum --check --quiet
podman run --rm -v "$PWD:/work" -w /work "$image" \
  Rscript scripts/comparison/install_fakedatar.R "$library_dir" "$tarball"
podman run --rm -v "$PWD:/work" -w /work "$image" \
  Rscript scripts/comparison/check_fakedatar.R "$library_dir" "$input" "$run_dir"

podman run --rm -v "$PWD:/work" -v "$(command -v uv):/usr/local/bin/uv:ro" -w /work \
  -e UV_PROJECT_ENVIRONMENT=/work/sdg_output/r_check/venv \
  -e UV_CACHE_DIR=/work/sdg_output/r_check/uv_cache \
  -e UV_PYTHON_INSTALL_DIR=/work/sdg_output/r_check/python \
  -e SDGR_COMMAND="uv run --quiet --frozen --project /work sdg" \
  "$image" Rscript scripts/comparison/check_sdgr.R sdg_output/r_check/sdgr_lib "$input" "$run_dir"

uv run python scripts/comparison/capability_table.py "$run_dir"
echo "Run folder: $run_dir"
