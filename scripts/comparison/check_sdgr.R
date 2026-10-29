# Run the R check W3 of capabilities.txt on sdg through the R package sdgr.
#
# The script runs in the rocker/r-ver container of run_capabilities.sh, after
# check_sdg.py. It installs r/sdgr into a mounted library, makes a config from
# the invented visits table as a data frame and reads the synthetic data back.
# It also reads the RDS output of the O4 check with readRDS. It adds the W3 row
# to RUN_DIR/sdg.tsv.
#
# Usage: Rscript check_sdgr.R LIBRARY INPUT_DIR RUN_DIR

args <- commandArgs(trailingOnly = TRUE)
dir.create(args[[1]], showWarnings = FALSE, recursive = TRUE)
library_dir <- normalizePath(args[[1]])
input_dir <- normalizePath(args[[2]])
run_dir <- normalizePath(args[[3]])

.libPaths(c(library_dir, .libPaths()))
install.packages("r/sdgr", lib = library_dir, repos = NULL, type = "source", quiet = TRUE)
library(sdgr)

check_w3 <- function() {
  visits <- utils::read.csv(file.path(input_dir, "visits.csv"), stringsAsFactors = FALSE)
  config <- sdg_profile(visits, name = "visits.csv", seed = 42)
  synthetic <- sdg_data(config)[[1]]
  rds <- file.path(run_dir, "sdg_work", "rds", "data", "file_1.rds")
  rds_rows <- if (file.exists(rds)) nrow(readRDS(rds)) else NA_integer_
  passed <- identical(names(synthetic), names(visits)) && nrow(synthetic) == 200L && identical(rds_rows, 200L)
  note <- sprintf("sdgr %s: sdg_profile and sdg_data on a data frame; readRDS reads %s rows of the O4 file",
                  utils::packageVersion("sdgr"), rds_rows)
  list(passed = passed, note = note)
}

failed_check <- function(error) {
  list(passed = FALSE, note = paste("error:", conditionMessage(error)))
}

outcome <- tryCatch(check_w3(), error = failed_check)
result <- if (outcome$passed) "pass" else "fail"
cat(sprintf("sdg W3: %s (%s)\n", result, outcome$note))
cat(sprintf("W3\t%s\t%s\n", result, gsub("[\t\n]", " ", outcome$note)), file = file.path(run_dir, "sdg.tsv"),
    append = TRUE)
