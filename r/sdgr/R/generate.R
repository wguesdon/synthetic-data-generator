# The files of an sdg output folder that are not data.
SDG_FILES <- c("config.yaml", "manifest.json", "SYNTHETIC_DATA.md")

# The separators that sdgr tries on the header line of a text table.
SEPARATORS <- c(",", ";", "\t", "|")

#' Check a config
#'
#' @param config Path of the YAML config.
#' @return The summary lines of sdg, invisibly. The function stops with each
#'   issue of the config when the config is not valid.
#' @export
#' @examples
#' if (sdg_available()) {
#'   sdg_check(system.file("extdata", "visits.yaml", package = "sdgr"))
#' }
sdg_check <- function(config) {
  sdg_run(c("check", path.expand(config)))
}

#' Generate synthetic data from a config
#'
#' @param config Path of the YAML config.
#' @param output The output folder. It must be empty or an earlier sdg output.
#' @return The output folder, invisibly. The folder holds the data files, the
#'   truth tables, the config and the manifest.
#' @export
#' @examples
#' if (sdg_available()) {
#'   folder <- sdg_generate(system.file("extdata", "visits.yaml", package = "sdgr"))
#'   list.files(folder, recursive = TRUE)
#' }
sdg_generate <- function(config, output = tempfile("sdg_")) {
  sdg_run(c("generate", path.expand(config), "-o", path.expand(output)))
  invisible(output)
}

#' Read the tables of an sdg output folder
#'
#' The function reads each CSV, TSV, TXT and RDS file. It reads a Parquet file
#' when the package arrow is installed, and each sheet of an Excel file when
#' the package readxl is installed. It skips the other formats, such as FCS or
#' FASTQ. A text table must have its header in the first row.
#'
#' @param output The output folder of [sdg_generate()].
#' @param truth Also read the truth tables in the folder `truth/`.
#' @return A named list of data frames. Each name is the path in the folder,
#'   such as `"file_1.csv"` or `"panel.xlsx/Sheet1"`.
#' @export
#' @examples
#' if (sdg_available()) {
#'   folder <- sdg_generate(system.file("extdata", "visits.yaml", package = "sdgr"))
#'   str(sdg_read(folder))
#' }
sdg_read <- function(output, truth = FALSE) {
  if (!dir.exists(output)) {
    stop("No output folder: ", output, call. = FALSE)
  }
  files <- sort(list.files(output, recursive = TRUE))
  files <- files[!files %in% SDG_FILES]
  if (!truth) {
    files <- files[!startsWith(files, "truth/")]
  }
  tables <- list()
  for (file in files) {
    tables <- c(tables, read_data_file(file.path(output, file), file))
  }
  tables
}

#' Generate a config and read its tables
#'
#' The function writes the data into a new temporary folder, like the pytest
#' fixture `sdg_data` of sdg.
#'
#' @inheritParams sdg_check
#' @inheritParams sdg_read
#' @return A named list of data frames, as [sdg_read()] returns.
#' @export
#' @examples
#' if (sdg_available()) {
#'   tables <- sdg_data(system.file("extdata", "visits.yaml", package = "sdgr"))
#'   head(tables[["visits.csv"]])
#' }
sdg_data <- function(config, truth = FALSE) {
  sdg_read(sdg_generate(config), truth = truth)
}

#' Read one data file of an output folder
#'
#' @param path Path of the file.
#' @param name Name of the file in the output folder.
#' @return A named list of the data frames of the file. The list is empty for
#'   a format that sdgr does not read.
#' @noRd
read_data_file <- function(path, name) {
  suffix <- tolower(tools::file_ext(path))
  if (suffix %in% c("csv", "tsv", "txt")) {
    separator <- if (suffix == "tsv") "\t" else guess_separator(path)
    frame <- utils::read.csv(path, sep = separator, check.names = FALSE, stringsAsFactors = FALSE)
    return(named_frames(list(frame), name))
  }
  if (suffix == "rds") {
    return(named_frames(list(as.data.frame(readRDS(path))), name))
  }
  if (suffix == "parquet") {
    return(read_parquet_file(path, name))
  }
  if (suffix %in% c("xlsx", "xlsm")) {
    return(read_excel_file(path, name))
  }
  list()
}

#' Name a list of data frames
#'
#' @param frames The list.
#' @param names The names.
#' @return The named list.
#' @noRd
named_frames <- function(frames, names) {
  names(frames) <- names
  frames
}

#' Read a Parquet file when arrow is installed
#'
#' @inheritParams read_data_file
#' @return A named list with one data frame, or an empty list.
#' @noRd
read_parquet_file <- function(path, name) {
  if (!requireNamespace("arrow", quietly = TRUE)) {
    message("Skipped ", name, ": install the package arrow to read Parquet.")
    return(list())
  }
  named_frames(list(as.data.frame(arrow::read_parquet(path))), name)
}

#' Read each sheet of an Excel file when readxl is installed
#'
#' @inheritParams read_data_file
#' @return A named list with one data frame per sheet, or an empty list.
#' @noRd
read_excel_file <- function(path, name) {
  if (!requireNamespace("readxl", quietly = TRUE)) {
    message("Skipped ", name, ": install the package readxl to read Excel.")
    return(list())
  }
  sheets <- readxl::excel_sheets(path)
  frames <- list()
  for (sheet in sheets) {
    frames[[length(frames) + 1L]] <- as.data.frame(readxl::read_excel(path, sheet = sheet))
  }
  named_frames(frames, paste0(name, "/", sheets))
}

#' Guess the separator of a text table from its header line
#'
#' @param path Path of the file.
#' @return The separator that occurs most often in the first line.
#' @noRd
guess_separator <- function(path) {
  first <- readLines(path, n = 1L, warn = FALSE)
  if (!length(first)) {
    return(",")
  }
  counts <- vapply(SEPARATORS, count_separator, integer(1), line = first)
  SEPARATORS[[which.max(counts)]]
}

#' Count a separator in a line
#'
#' @param separator The separator.
#' @param line The line.
#' @return The number of times the separator occurs.
#' @noRd
count_separator <- function(separator, line) {
  nchar(line) - nchar(gsub(separator, "", line, fixed = TRUE))
}
