#!/usr/bin/env bash
# End-to-end test on public datasets of real deliveries.
#
# The script downloads public files of each format into input/public/, which
# git ignores, and runs the whole chain on each case: glimpse, audit of the
# glimpse, profile, audit of the profile, check, generate, glimpse of the
# synthetic data, compare and validate. docs/public_data.md lists the sources.
#
# Each case also has a fact of its known structure, such as "samples: 107",
# that the glimpse of the real file must print. A misread file fails this
# "structure" step, even when the rest of the chain agrees with itself.
#
# Each case has an expected result: "pass", or the step that must fail, with
# the reason in docs/public_data.md. The script fails when a result differs
# from its expectation, so a fix of a known limit also shows.
#
#   tests/public_data_test.sh             every case
#   tests/public_data_test.sh olink nano  the cases whose name holds a text
#
# The downloads need curl and the GitHub CLI gh. Outputs go to
# sdg_output/public_<timestamp>/. Nothing is deleted.
set -uo pipefail

cd "$(dirname "$0")/.."

base=input/public
raw=https://raw.githubusercontent.com
olink_sha=7385f477050b65e9470e709d30b1b23c6fcb5702
nanotube_sha=cf02f2e565efab61e4cd8a7c9581da105669f795
qcpro_sha=ef0b3d613c7de2e6f6cb882e224df72d16d1095c
flowdata_sha=dad6b3b06c9477592f6f336fd37a5f79bcec6bf1
fcsparser_sha=da70aaa7ec92ff3bd9ce00aec4eea7c77ee8c096
cellxgene_sha=76a39fcf92da57c9e4fd59831ad805a3b007da8c
nfcore_sha=2577e91d3dc0e617c46c9c69cfce1cc3b04c17b4
datahub_sha=18763f8eee86d4886d7210068f745f1e509498ee
penguins_sha=8957207b78d6ccd1b4654a9dd9c9041b657478ab
uci=https://archive.ics.uci.edu/ml/machine-learning-databases
mimic=https://physionet.org/files/mimic-iv-demo/2.2/hosp

# Download one URL into a dataset folder, unless the file is there.
fetch() {
  local folder="$base/$1" url="$2" name="${3:-$(basename "$2")}"
  mkdir -p "$folder"
  [ -s "$folder/$name" ] && return 0
  echo "download $1/$name"
  curl -sSfL --retry 3 --max-time 600 -o "$folder/$name" "$url"
}

# Download every file of a GitHub folder at a commit whose path matches a pattern.
fetch_tree() {
  local folder="$1" repo="$2" sha="$3" prefix="$4" pattern="$5" path
  [ -n "$(ls -A "$base/$folder" 2>/dev/null)" ] && return 0
  while IFS= read -r path; do
    fetch "$folder" "$raw/$repo/$sha/${path// /%20}" "$(basename "$path")"
  done < <(gh api "repos/$repo/git/trees/$sha?recursive=1" \
    --jq ".tree[] | select(.type==\"blob\") | .path | select(startswith(\"$prefix\")) | select(test(\"$pattern\"))")
}

# Download a gzip file and keep an unpacked copy beside it.
fetch_gzip() {
  fetch "$1" "$2"
  local packed
  packed="$base/$1/$(basename "$2")"
  [ -s "${packed%.gz}" ] || gunzip -k "$packed"
}

# Save a copy of an .xls workbook as .xlsx with LibreOffice, when it is installed.
convert_xls() {
  local source="$base/$1/$3.xls" folder="$base/$2"
  command -v soffice >/dev/null || return 0
  [ -s "$folder/$3.xlsx" ] && return 0
  mkdir -p "$folder"
  echo "convert $2/$3.xlsx"
  soffice --headless --convert-to xlsx --outdir "$folder" "$source" >/dev/null
}

download() {
  fetch tcga_cdr_xlsx "https://api.gdc.cancer.gov/data/1b5f413e-a8d1-4d10-92eb-7c4ae739ed81" \
    TCGA-CDR-SupplementalTableS1.xlsx
  fetch cbioportal_brca_tsv "https://media.githubusercontent.com/media/cBioPortal/datahub/$datahub_sha/public/brca_tcga_pan_can_atlas_2018/data_clinical_patient.txt"
  fetch penguins_raw_csv "$raw/allisonhorst/palmerpenguins/$penguins_sha/inst/extdata/penguins_raw.csv"
  local table
  for table in patients admissions labevents; do
    fetch_gzip "mimic_${table}_csv" "$mimic/$table.csv.gz"
  done
  fetch cervical_cancer_csv "$uci/00383/risk_factors_cervical_cancer.csv"
  fetch heart_failure_csv "$uci/00519/heart_failure_clinical_records_dataset.csv"
  fetch credit_default_xls "$uci/00350/default%20of%20credit%20card%20clients.xls" default_of_credit_card_clients.xls
  fetch real_estate_xlsx "$uci/00477/Real%20estate%20valuation%20data%20set.xlsx" real_estate_valuation.xlsx
  convert_xls credit_default_xls credit_default_xlsx default_of_credit_card_clients
  local olink="$raw/Olink-Proteomics/OlinkRPackage/$olink_sha/OlinkAnalyze/inst/extdata"
  fetch olink_long_csv "$olink/npx_data_long_csv.csv"
  fetch olink_ext_parquet "$olink/npx_data_ext.parquet"
  fetch olink_npx1_xlsx "$olink/npx_data1.xlsx"
  fetch olink_npx1_wide_xlsx "$olink/npx_data1_original.xlsx"
  fetch_gzip gse60450_counts \
    "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE60nnn/GSE60450/suppl/GSE60450_Lactation-GenewiseCounts.txt.gz"
  fetch_gzip gse10072_microarray \
    "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE10nnn/GSE10072/matrix/GSE10072_series_matrix.txt.gz"
  fetch pbmc3k_processed_h5ad "$raw/chanzuckerberg/cellxgene/$cellxgene_sha/example-dataset/pbmc3k.h5ad"
  fetch pbmc3k_raw_h5ad "https://falexwolf.de/data/pbmc3k_raw.h5ad"
  fetch_tree nanotube_rcc bioc/NanoTube "$nanotube_sha" inst/extdata/GSE117751_RAW/ '\\.RCC$'
  fetch_tree nanostringqcpro_rcc bioc/NanoStringQCPro "$qcpro_sha" inst/extdata/RCC/ '\\.RCC$'
  fetch_tree fcs_cytotrol bioc/flowWorkspaceData "$flowdata_sha" inst/extdata/CytoTrol 'fcs$'
  fetch_tree fcs_instruments eyurtsev/fcsparser "$fcsparser_sha" fcsparser/tests/data/FlowCytometers/ '\\.fcs$'
  local nfcore="$raw/nf-core/test-datasets/$nfcore_sha/data/genomics/homo_sapiens"
  fetch fastq_pair "$nfcore/illumina/fastq/test_1.fastq.gz"
  fetch fastq_pair "$nfcore/illumina/fastq/test_2.fastq.gz"
  fetch fastq_reference "$nfcore/genome/genome.fasta"
}

# Each case: name|expected result|known structure fact|sdg options|targets,
# with targets split on commas.
cases=(
  "tcga_cdr_xlsx|compare|rows: 11160; columns: 34||$base/tcga_cdr_xlsx/TCGA-CDR-SupplementalTableS1.xlsx"
  "cbioportal_brca_tsv|pass|rows: 1084; columns: 38;||$base/cbioportal_brca_tsv/data_clinical_patient.txt"
  "penguins_raw_csv|pass|rows: 344; columns: 17||$base/penguins_raw_csv/penguins_raw.csv"
  "mimic_patients_csv|pass|rows: 100; columns: 6||$base/mimic_patients_csv/patients.csv"
  "mimic_admissions_csv|pass|rows: 275; columns: 16||$base/mimic_admissions_csv/admissions.csv"
  "mimic_labevents_csv|pass|rows: 107727; columns: 16||$base/mimic_labevents_csv/labevents.csv"
  "cervical_cancer_csv|pass|rows: 858; columns: 36||$base/cervical_cancer_csv/risk_factors_cervical_cancer.csv"
  "heart_failure_csv|pass|rows: 299; columns: 13||$base/heart_failure_csv/heart_failure_clinical_records_dataset.csv"
  "credit_default_xls|glimpse|rows: 30000; columns: 25||$base/credit_default_xls/default_of_credit_card_clients.xls"
  "real_estate_xlsx|pass|rows: 414; columns: 8||$base/real_estate_xlsx/real_estate_valuation.xlsx"
  "olink_long_csv|compare|samples: 4; assays: 906||$base/olink_long_csv/npx_data_long_csv.csv"
  "olink_ext_parquet|audit_glimpse|samples: 1; assays: 1||$base/olink_ext_parquet/npx_data_ext.parquet"
  "olink_npx1_xlsx|pass|samples: 160; assays: 184||$base/olink_npx1_xlsx/npx_data1.xlsx"
  "olink_npx1_wide_xlsx|pass|samples: 160; assays: 1104||$base/olink_npx1_wide_xlsx/npx_data1_original.xlsx"
  "gse60450_counts|pass|features: 27179; samples: 12;|--format counts|$base/gse60450_counts/GSE60450_Lactation-GenewiseCounts.txt"
  "gse10072_microarray|pass|features: 22283; samples: 107;|--format microarray|$base/gse10072_microarray/GSE10072_series_matrix.txt"
  "pbmc3k_processed_h5ad|pass|rows: 2638; features: 1838||$base/pbmc3k_processed_h5ad/pbmc3k.h5ad"
  "pbmc3k_raw_h5ad|pass|rows: 2700; features: 32738||$base/pbmc3k_raw_h5ad/pbmc3k_raw.h5ad"
  "nanotube_rcc|pass|rows: 42; codes: 608||$base/nanotube_rcc"
  "nanostringqcpro_rcc|pass|rows: 25; codes: 32||$base/nanostringqcpro_rcc"
  "fcs_cytotrol|pass|rows: 2; channels: 12||$base/fcs_cytotrol"
  "fastq_pair|pass|paired: true; mate: 1|--set reference=$base/fastq_reference/genome.fasta|$base/fastq_pair"
)

# The .xlsx copy of the credit workbook needs LibreOffice to convert it.
if command -v soffice >/dev/null; then
  cases+=("credit_default_xlsx|pass|header row: 2; private: safe facts; private columns: 'SEX', 'AGE'||$base/credit_default_xlsx/default_of_credit_card_clients.xlsx")
fi

# Files of the fcsparser test set that no reader must accept, or that flowio
# cannot read; every other file of the set must pass.
declare -A fcs_expected=(
  [Cytek_xP5]=glimpse [Guava_Muse]=glimpse [corrupted]=glimpse [fake_large_fcs]=glimpse
  [sample_header]=glimpse
)

# Print the data files and folders of an output folder.
data_targets() {
  find "$1" -mindepth 1 -maxdepth 1 \
    ! -name config.yaml ! -name SYNTHETIC_DATA.md ! -name manifest.json ! -name truth | sort
}

# Run one step with its output in <step>.out and <step>.err; return its exit code.
step() {
  local out="$1" name="$2"
  shift 2
  "$@" > "$out/$name.out" 2> "$out/$name.err"
}

# Run the chain on one case and print the first failed step, or "pass".
run_case() {
  local out="$1" truth="$2" opts="$3" targets="$4" files options synthetic
  IFS=',' read -r -a files <<< "$targets"
  read -r -a options <<< "$opts"
  step "$out" glimpse uv run sdg glimpse "${options[@]}" "${files[@]}" || { echo glimpse; return; }
  grep -qF -- "$truth" "$out/glimpse.out" || { echo structure; return; }
  step "$out" audit_glimpse uv run sdg audit "$out/glimpse.out" "${options[@]}" "${files[@]}" ||
    { echo audit_glimpse; return; }
  step "$out" profile uv run sdg profile "${options[@]}" "${files[@]}" -o "$out/profile.yaml" ||
    { echo profile; return; }
  step "$out" audit_profile uv run sdg audit "$out/profile.yaml" "${options[@]}" "${files[@]}" ||
    { echo audit_profile; return; }
  step "$out" check uv run sdg check "$out/profile.yaml" || { echo check; return; }
  step "$out" generate uv run sdg generate "$out/profile.yaml" -o "$out/synthetic" || { echo generate; return; }
  mapfile -t synthetic < <(data_targets "$out/synthetic")
  step "$out" glimpse_synthetic uv run sdg glimpse "${options[@]}" "${synthetic[@]}" ||
    { echo glimpse_synthetic; return; }
  step "$out" compare uv run sdg compare "$out/glimpse.out" "$out/glimpse_synthetic.out" || { echo compare; return; }
  step "$out" validate uv run sdg validate "$out/synthetic" || { echo validate; return; }
  echo pass
}

download || { echo "a download failed" >&2; exit 1; }
for path in "$base"/fcs_instruments/*.fcs; do
  stem="$(basename "$path" .fcs)"
  stem="${stem// /_}"
  cases+=("fcs_$stem|${fcs_expected[$stem]:-pass}|rows: 1;||$path")
done

work="sdg_output/public_$(date +%Y_%m_%d_%H%M%S)"
mkdir -p "$work"
echo "work folder: $work"
sha256sum "$base"/*/* > "$work/inputs.sha256"

mismatched=()
count=0
for entry in "${cases[@]}"; do
  IFS='|' read -r name expected truth opts targets <<< "$entry"
  if [ "$#" -gt 0 ]; then
    selected=false
    for text in "$@"; do [[ "$name" == *"$text"* ]] && selected=true; done
    $selected || continue
  fi
  count=$((count + 1))
  mkdir -p "$work/$name"
  result="$(run_case "$work/$name" "$truth" "$opts" "$targets")"
  if [ "$result" = "$expected" ]; then
    echo "ok        $name: $result"
  else
    echo "MISMATCH  $name: expected $expected, got $result; see $work/$name"
    mismatched+=("$name")
  fi
done

if [ "${#mismatched[@]}" -gt 0 ]; then
  echo "mismatched: ${mismatched[*]}" >&2
  exit 1
fi
echo "all $count case(s) gave the expected result"
