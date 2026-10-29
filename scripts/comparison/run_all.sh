#!/usr/bin/env bash
# Run the comparison of sdg, FakeDataR and Faker on the public cases.
#
# The script fetches the one public file that tests/public_data_test.sh does not
# fetch, runs each tool and measures each output. The outputs go to
# sdg_output/comparison/run_<timestamp>/. docs/comparison.md gives the result.
#
# Usage: scripts/comparison/run_all.sh
set -euo pipefail

run_dir=sdg_output/comparison/run_$(date +%Y_%m_%d_%H%M%S)
titanic=input/public/titanic_csv/titanic.csv
titanic_url=https://raw.githubusercontent.com/datasciencedojo/datasets/4cd38e7a532643145d00c1e512b9d16899ae9543/titanic.csv
titanic_sha256=4a437fde05fe5264e1701a7387ac6fb75393772ba38bb2c9c566405af5af4bd7

tail -n +2 scripts/comparison/cases.txt | cut -f2 | while read -r file; do
  if [[ ! -f $file && $file != "$titanic" ]]; then
    echo "Missing $file. Run tests/public_data_test.sh first to fetch the public files." >&2
    exit 1
  fi
done
if [[ ! -f $titanic ]]; then
  mkdir -p "$(dirname "$titanic")"
  curl -sSfL -o "$titanic" "$titanic_url"
fi
echo "$titanic_sha256  $titanic" | sha256sum --check --quiet

mkdir -p "$run_dir"
scripts/comparison/run_sdg.sh "$run_dir"
scripts/comparison/run_fakedatar.sh "$run_dir"
uv run scripts/comparison/run_faker.py "$run_dir"
uv run python scripts/comparison/measure.py "$run_dir"
echo "Run folder: $run_dir"
