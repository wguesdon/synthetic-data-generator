# Write the help files of r/sdgr, then build and check the package.
#
# The script runs in the rocker/r-ver container of test_r_package.sh. It
# installs roxygen2 and testthat into the mounted library, writes NAMESPACE
# and man/ with roxygen2, and runs R CMD build and R CMD check with the tests.
#
# Usage: Rscript scripts/check_r_package.R LIBRARY WORK_FOLDER

args <- commandArgs(trailingOnly = TRUE)
dir.create(args[[1]], showWarnings = FALSE, recursive = TRUE)
dir.create(args[[2]], showWarnings = FALSE, recursive = TRUE)
library_dir <- normalizePath(args[[1]])
work_dir <- normalizePath(args[[2]])
package_dir <- normalizePath("r/sdgr")

.libPaths(c(library_dir, .libPaths()))
needed <- c("roxygen2", "testthat")
missing <- needed[!vapply(needed, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing)) install.packages(missing, lib = library_dir)

roxygen2::roxygenise(package_dir)

setwd(work_dir)
unlink(list.files(work_dir, pattern = "^sdgr_.*\\.tar\\.gz$"))
status <- system2(file.path(R.home("bin"), "R"), c("CMD", "build", "--no-manual", shQuote(package_dir)))
if (status != 0L) stop("R CMD build failed")
tarball <- list.files(work_dir, pattern = "^sdgr_.*\\.tar\\.gz$")
Sys.setenv(R_LIBS_USER = library_dir, `_R_CHECK_FORCE_SUGGESTS_` = "false")
status <- system2(file.path(R.home("bin"), "R"), c("CMD", "check", "--no-manual", tarball))
if (status != 0L) stop("R CMD check failed")
cat(readLines(file.path(work_dir, "sdgr.Rcheck", "tests", "testthat.Rout")), sep = "\n")
