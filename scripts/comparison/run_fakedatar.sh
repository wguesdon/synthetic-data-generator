#!/usr/bin/env bash
# Run FakeDataR 0.2.2 in a rocker/r-ver container with Podman.
#
# The script downloads the CRAN source tarball, checks its SHA-256 hash,
# installs the package into sdg_output/comparison/r_lib/ and runs
# run_fakedatar.R. R is not installed on the host.
#
# Usage: scripts/comparison/run_fakedatar.sh RUN_DIR
set -euo pipefail

run_dir=${1:?usage: run_fakedatar.sh RUN_DIR}
image=docker.io/rocker/r-ver:4.5.1
source_dir=sdg_output/comparison/fakedatar_src
tarball=$source_dir/FakeDataR_0.2.2.tar.gz
tarball_sha256=$(cat scripts/comparison/fakedatar_0.2.2.sha256)
library_dir=sdg_output/comparison/r_lib

mkdir -p "$source_dir" "$library_dir"
if [[ ! -f $tarball ]]; then
  curl -sSfL -o "$tarball" https://cran.r-project.org/src/contrib/Archive/FakeDataR/FakeDataR_0.2.2.tar.gz \
    || curl -sSfL -o "$tarball" https://cran.r-project.org/src/contrib/FakeDataR_0.2.2.tar.gz
fi
echo "$tarball_sha256  $tarball" | sha256sum --check --quiet

podman run --rm -v "$PWD:/work" -w /work "$image" \
  Rscript scripts/comparison/install_fakedatar.R "$library_dir" "$tarball"
podman run --rm -v "$PWD:/work" -w /work "$image" \
  Rscript scripts/comparison/run_fakedatar.R "$library_dir" "$run_dir"
