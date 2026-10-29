#!/usr/bin/env bash
# Run sdg 0.8.0 on each comparison case in three variants.
#
#   sdg_real      the default rules, with the real names and levels
#   sdg_generic   the default rules, with --generic-labels
#   sdg_rule_off  the private-column rule switched off, see sdg_rule_off.py
#   sdg_model     the penguin table only: the rule switched off, then the model
#                 mode with the effects of the groups report
#
# Each case folder gets shared/ (the report and the config, which leave the
# secure machine), generated/ (the output of sdg generate) and synthetic.csv.
#
# Usage: scripts/comparison/run_sdg.sh RUN_DIR
set -euo pipefail

run_dir=${1:?usage: run_sdg.sh RUN_DIR}
cases=scripts/comparison/cases.txt
seed=42

run_variant() {
  local variant=$1 case=$2 file=$3
  local out="$run_dir/$variant/$case"
  local -a sdg=(uv run sdg) options=()
  case $variant in
    sdg_generic) options=(--generic-labels) ;;
    sdg_rule_off) sdg=(uv run python scripts/comparison/sdg_rule_off.py); options=(--private '^$') ;;
  esac
  mkdir -p "$out/shared"
  # The report command writes no report when its audit fails; the log keeps the reason.
  "${sdg[@]}" report "$file" "${options[@]}" -o "$out/shared/" > "$out/report.log" 2>&1 \
    || echo "$variant $case: the report audit failed, see $out/report.log"
  if [[ $variant == sdg_generic ]]; then
    "${sdg[@]}" profile "$file" "${options[@]}" --label-key "$out/key.csv" --seed "$seed" \
      -o "$out/shared/config.yaml" > "$out/profile.log" 2>&1
  else
    "${sdg[@]}" profile "$file" "${options[@]}" --seed "$seed" -o "$out/shared/config.yaml" > "$out/profile.log" 2>&1
  fi
  uv run sdg generate "$out/shared/config.yaml" -o "$out/generated" > "$out/generate.log" 2>&1
  cp "$out/generated/file_1.csv" "$out/synthetic.csv"
  echo "$variant $case: done"
}

# The model mode on the penguin table, with the rule switched off: the groups
# report gives the ratio of each species against the reference, and
# make_model_config.py plants each ratio as an effect, as the agent does.
run_model() {
  local case=penguins_raw file=$1 group=Species
  local out="$run_dir/sdg_model/$case"
  mkdir -p "$out/shared"
  uv run python scripts/comparison/sdg_rule_off.py describe groups "$file" --group "$group" --subject "" \
    --private '^$' -o "$out/shared/" > "$out/groups.log" 2>&1
  uv run python scripts/comparison/make_model_config.py "$run_dir/sdg_rule_off/$case/shared/config.yaml" \
    "$out"/shared/groups_*.txt "$group" "$out/shared/config.yaml" > "$out/model.log" 2>&1
  uv run sdg check "$out/shared/config.yaml" >> "$out/model.log" 2>&1
  uv run sdg generate "$out/shared/config.yaml" -o "$out/generated" > "$out/generate.log" 2>&1
  uv run sdg validate "$out/generated" > "$out/validate.log" 2>&1 || echo "sdg_model $case: see $out/validate.log"
  cp "$out/generated/file_1.csv" "$out/synthetic.csv"
  echo "sdg_model $case: done"
}

tail -n +2 "$cases" | while IFS=$'\t' read -r case file; do
  for variant in sdg_real sdg_generic sdg_rule_off; do
    run_variant "$variant" "$case" "$file"
  done
done
run_model "$(awk -F'\t' '$1 == "penguins_raw" {print $2}' "$cases")"
