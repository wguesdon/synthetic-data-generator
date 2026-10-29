# Install FakeDataR 0.2.2 and its imports into a mounted library folder.
#
# The rocker/r-ver image pins its CRAN repository to a dated snapshot, so the
# imports have fixed versions. FakeDataR itself comes from the CRAN source
# tarball that run_fakedatar.sh downloads and checks.
#
# Usage: Rscript install_fakedatar.R LIBRARY TARBALL

args <- commandArgs(trailingOnly = TRUE)
library_dir <- args[[1]]
tarball <- args[[2]]

dir.create(library_dir, showWarnings = FALSE, recursive = TRUE)
.libPaths(c(library_dir, .libPaths()))

needed <- c("dplyr", "jsonlite", "zip", "readr", "DBI", "RSQLite", "arrow")
missing <- needed[!vapply(needed, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing)) install.packages(missing, lib = library_dir)

if (!requireNamespace("FakeDataR", quietly = TRUE) ||
    utils::packageVersion("FakeDataR") != "0.2.2") {
  install.packages(tarball, lib = library_dir, repos = NULL, type = "source")
}

cat("FakeDataR", as.character(utils::packageVersion("FakeDataR")), "\n")
cat("repository", getOption("repos")[["CRAN"]], "\n")
