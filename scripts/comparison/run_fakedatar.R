# Run FakeDataR 0.2.2 on each comparison case at the three privacy levels.
#
# For each case and level, llm_bundle() writes its default bundle (CSV, RDS,
# JSON schema and README_FOR_LLM.txt) into <level>/<case>/shared/. The script
# also writes the attributes of the RDS file as text, so that the measure can
# search them, and copies the CSV to synthetic.csv.
#
# The real table is read with readr, and each whole-number column becomes an
# integer column. readr reads every number as a double, and FakeDataR then
# draws decimals in a column of counts. The conversion gives FakeDataR the
# best input classes.
#
# Usage: Rscript run_fakedatar.R LIBRARY RUN_DIR [CASES]

args <- commandArgs(trailingOnly = TRUE)
.libPaths(c(args[[1]], .libPaths()))
run_dir <- args[[2]]
seed <- 42

suppressPackageStartupMessages(library(FakeDataR))

#' Read a real table with readr and make whole-number columns integer.
read_real <- function(path) {
  real <- as.data.frame(readr::read_csv(path, show_col_types = FALSE, guess_max = 1e6,
                                        progress = FALSE))
  for (name in names(real)) {
    column <- real[[name]]
    if (is.double(column) && !inherits(column, c("Date", "POSIXct")) &&
        all(is.na(column) | column == round(column)) &&
        all(abs(column) < .Machine$integer.max, na.rm = TRUE)) {
      real[[name]] <- as.integer(column)
    }
  }
  real
}

#' Write the attributes of an RDS data frame, other than its names and rows, as text.
write_rds_text <- function(rds_path, text_path) {
  fake <- readRDS(rds_path)
  extra <- attributes(fake)
  extra <- extra[setdiff(names(extra), c("names", "row.names", "class"))]
  writeLines(utils::capture.output(dput(extra)), text_path)
}

case_file <- if (length(args) > 2) args[[3]] else "scripts/comparison/cases.txt"
cases <- utils::read.delim(case_file, stringsAsFactors = FALSE)
for (i in seq_len(nrow(cases))) {
  real <- read_real(cases$file[[i]])
  for (level in c("low", "medium", "high")) {
    out <- file.path(run_dir, paste0("fakedatar_", level), cases$case[[i]])
    shared <- file.path(out, "shared")
    dir.create(shared, showWarnings = FALSE, recursive = TRUE)
    bundle <- llm_bundle(real, n = nrow(real), level = level, formats = c("csv", "rds"),
                         path = shared, filename = "fake_bundle", seed = seed)
    file.copy(bundle$data_paths$csv, file.path(out, "synthetic.csv"), overwrite = TRUE)
    write_rds_text(bundle$data_paths$rds, file.path(shared, "fake_bundle_rds_attributes.txt"))
    cat("fakedatar_", level, " ", cases$case[[i]], ": done\n", sep = "")
  }
}
