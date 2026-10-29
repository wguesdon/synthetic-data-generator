#!/usr/bin/env bash
# Test the person values of sdg, Faker and FakeDataR on the Synthea and Titanic tables.
#
# The Synthea sample is public and fully synthetic: names, addresses, SSN,
# passport numbers, an encounter table whose PATIENT column names a patient,
# and start and stop times. The script fetches it at a pinned commit, runs each
# tool and writes person_values.md. The outputs go to
# sdg_output/comparison/person_values/run_<timestamp>/.
#
# Usage: scripts/comparison/run_person_values.sh
set -euo pipefail

run_dir=sdg_output/comparison/person_values/run_$(date +%Y_%m_%d_%H%M%S)
cases=scripts/comparison/person_cases.txt
download=input/public/synthea_download
archive=$download/synthea_sample_data_csv_apr2020.zip
archive_url=https://raw.githubusercontent.com/synthetichealth/synthea-sample-data/0b54a64fa722861f2fdeee824a2b9efa5291747b/downloads/synthea_sample_data_csv_apr2020.zip
archive_sha256=4194b18c11eaedcf0d5d5dd448d8ac9661f14381e2ef9f109215dc42266cd38a
image=docker.io/rocker/r-ver:4.5.1
library_dir=sdg_output/comparison/r_lib
tarball=sdg_output/comparison/fakedatar_src/FakeDataR_0.2.2.tar.gz

mkdir -p "$download" input/public/synthea_csv "$run_dir"
if [[ ! -f $archive ]]; then
  curl -sSfL -o "$archive" "$archive_url"
fi
echo "$archive_sha256  $archive" | sha256sum --check --quiet
unzip -o -j -q "$archive" csv/patients.csv csv/encounters.csv -d input/public/synthea_csv
if [[ ! -f input/public/titanic_csv/titanic.csv ]]; then
  echo "Run scripts/comparison/run_all.sh one time to fetch the Titanic file." >&2
  exit 1
fi

# sdg reads the two Synthea tables in one run, so the encounters link to the patients.
for case in synthea:input/public/synthea_csv titanic:input/public/titanic_csv/titanic.csv; do
  name=${case%%:*}
  mkdir -p "$run_dir/sdg/$name"
  uv run sdg profile "${case#*:}" --seed 42 -o "$run_dir/sdg/$name/config.yaml" 2>/dev/null
  uv run sdg generate "$run_dir/sdg/$name/config.yaml" -o "$run_dir/sdg/$name/data" >/dev/null
done

uv run scripts/comparison/run_faker.py "$run_dir" "$cases"

podman run --rm -v "$PWD:/work" -w /work "$image" \
  Rscript scripts/comparison/install_fakedatar.R "$library_dir" "$tarball"
podman run --rm -v "$PWD:/work" -w /work "$image" \
  Rscript scripts/comparison/run_fakedatar.R "$library_dir" "$run_dir" "$cases"

uv run python scripts/comparison/person_values.py "$run_dir"
echo "Run folder: $run_dir"
