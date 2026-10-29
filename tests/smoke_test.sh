#!/usr/bin/env bash
# End-to-end test over each example config.
#
# For each config in example/configs/, the first generated output stands in for
# a real delivery. The test then runs the whole chain on it: glimpse, profile,
# audit of the profile and of the glimpse, a second generation from the
# profile, a compare of the two glimpse reports, and a validate of both outputs.
#
#   tests/smoke_test.sh            every example
#   tests/smoke_test.sh fcs_panel  one example
#
# Outputs go to sdg_output/smoke_<timestamp>/, which git ignores. Nothing is
# deleted.
set -euo pipefail

cd "$(dirname "$0")/.."

work="sdg_output/smoke_$(date +%Y_%m_%d_%H%M%S)"
mkdir -p "$work"
echo "work folder: $work"

# Print the data files and folders of an output folder, without the files that
# sdg adds around the data.
data_targets() {
  find "$1" -mindepth 1 -maxdepth 1 \
    ! -name config.yaml ! -name SYNTHETIC_DATA.md ! -name manifest.json ! -name truth | sort
}

configs=(example/configs/*.yaml)
if [ "$#" -gt 0 ]; then
  configs=()
  for name in "$@"; do configs+=("example/configs/$name.yaml"); done
fi

failed=()
for config in "${configs[@]}"; do
  name="$(basename "$config" .yaml)"
  out="$work/$name"
  format="$(sed -n 's/^ *format: *\([a-z0-9]*\).*/\1/p' "$config" | head -n 1)"
  options=()
  case "$format" in
    counts|microarray) options=(--format "$format") ;;
  esac
  echo "== $name ($format)"
  if ! {
    uv run sdg check "$config" >/dev/null &&
    uv run sdg generate "$config" -o "$out/first" >/dev/null &&
    mapfile -t first < <(data_targets "$out/first") &&
    uv run sdg glimpse "${options[@]}" "${first[@]}" > "$out/first_glimpse.txt" &&
    uv run sdg profile "${options[@]}" "${first[@]}" -o "$out/profile.yaml" 2>/dev/null &&
    uv run sdg audit "$out/profile.yaml" "${options[@]}" "${first[@]}" >/dev/null &&
    uv run sdg audit "$out/first_glimpse.txt" "${options[@]}" "${first[@]}" >/dev/null &&
    uv run sdg generate "$out/profile.yaml" -o "$out/second" >/dev/null &&
    mapfile -t second < <(data_targets "$out/second") &&
    uv run sdg glimpse "${options[@]}" "${second[@]}" > "$out/second_glimpse.txt" &&
    uv run sdg compare "$out/first_glimpse.txt" "$out/second_glimpse.txt" > "$out/compare.txt" &&
    uv run sdg validate "$out/first" > "$out/validate_first.txt" &&
    uv run sdg validate "$out/second" > "$out/validate_second.txt"
  }; then
    echo "   FAILED, see $out"
    failed+=("$name")
    continue
  fi
  echo "   ok: $(tail -n 1 "$out/compare.txt")"
done

if [ "${#failed[@]}" -gt 0 ]; then
  echo "failed: ${failed[*]}" >&2
  exit 1
fi
echo "all ${#configs[@]} example(s) passed"
