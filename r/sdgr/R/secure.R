#' Print a glimpse report of real data
#'
#' The report gives the structure of the data and no real value, so it can
#' leave the secure machine. A data frame goes to a temporary file, and the
#' function deletes the file before it returns.
#'
#' @param data A data frame, or the paths of files and folders.
#' @param name File name of a data frame, with the suffix `.csv`, `.tsv` or
#'   `.rds`. An RDS file needs the extra `sdg[r]` of sdg. A name that matches
#'   the private pattern makes the table private.
#' @param private A regular expression. A file whose name matches it is
#'   private: safe facts only, and counts only for its private columns. `NULL`
#'   keeps the default pattern of sdg, `"clinical"`.
#' @param generic_labels Replace the column names, the sheet names and the
#'   category labels with generic names.
#' @param drop A regular expression. sdg leaves out each column whose name
#'   matches it.
#' @param label_key Path of a CSV file for the key from each generic name to
#'   the real name. The key holds real names: keep it on the secure machine.
#' @param flags More arguments for sdg, such as `c("--max-categories", "5")`.
#' @return The lines of the report, with the class `sdg_text`.
#' @export
#' @examples
#' if (sdg_available()) {
#'   sdg_glimpse(airquality)
#' }
sdg_glimpse <- function(data, name = "dataframe.csv", private = NULL, generic_labels = FALSE, drop = NULL,
                        label_key = NULL, flags = character()) {
  folder <- tempfile("sdgr_")
  on.exit(unlink(folder, recursive = TRUE), add = TRUE)
  targets <- data_targets(data, folder, name)
  lines <- sdg_run(c("glimpse", targets, reading_args(private, generic_labels, drop, label_key, flags)))
  structure(lines, class = "sdg_text")
}

#' Write an audited report of real data
#'
#' The report holds the glimpse, the study designs and the group differences.
#' sdg audits the report for real values before it writes the file.
#'
#' @inheritParams sdg_glimpse
#' @param output The folder for the report. sdg names the file
#'   `report_<timestamp>.txt`.
#' @return The path of the report, invisibly.
#' @export
#' @examples
#' if (sdg_available()) {
#'   path <- sdg_report(airquality)
#'   readLines(path, n = 5)
#' }
sdg_report <- function(data, output = tempfile("sdg_report_"), name = "dataframe.csv", private = NULL,
                       generic_labels = FALSE, drop = NULL, label_key = NULL, flags = character()) {
  folder <- tempfile("sdgr_")
  on.exit(unlink(folder, recursive = TRUE), add = TRUE)
  targets <- data_targets(data, folder, name)
  dir.create(output, recursive = TRUE, showWarnings = FALSE)
  before <- list.files(output, full.names = TRUE)
  sdg_run(c("report", targets, reading_args(private, generic_labels, drop, label_key, flags),
            "-o", paste0(path.expand(output), "/")))
  written <- setdiff(list.files(output, full.names = TRUE), before)
  invisible(written[[length(written)]])
}

#' Write a config from real data
#'
#' The config holds the structure and the safe facts of the data, and no real
#' value. Read the config before it leaves the secure machine.
#'
#' @inheritParams sdg_glimpse
#' @param output Path of the YAML config.
#' @param seed Seed to write into the config. `NULL` lets sdg choose it.
#' @return The path of the config, invisibly.
#' @export
#' @examples
#' if (sdg_available()) {
#'   config <- sdg_profile(airquality)
#'   sdg_check(config)
#'   head(sdg_data(config)[[1]])
#' }
sdg_profile <- function(data, output = tempfile("sdg_config_", fileext = ".yaml"), name = "dataframe.csv",
                        private = NULL, generic_labels = FALSE, drop = NULL, seed = NULL, label_key = NULL,
                        flags = character()) {
  folder <- tempfile("sdgr_")
  on.exit(unlink(folder, recursive = TRUE), add = TRUE)
  targets <- data_targets(data, folder, name)
  args <- c("profile", targets, reading_args(private, generic_labels, drop, label_key, flags),
            "-o", path.expand(output))
  if (!is.null(seed)) {
    args <- c(args, "--seed", format(seed, scientific = FALSE))
  }
  sdg_run(args)
  invisible(output)
}

#' Write a Quarto report of data for the secure machine
#'
#' The report shows the column table, the first and the last rows, and a plot
#' for each numeric and category column of each table. With `synthetic`, each
#' table sits beside its synthetic table, and the report lists the
#' differences that `sdg compare` finds. The report holds real values: keep it
#' on the secure machine, and never paste it into an LLM.
#'
#' @inheritParams sdg_glimpse
#' @param synthetic The synthetic data: a data frame, or the paths of files or
#'   of an sdg output folder. `NULL` reports `data` alone.
#' @param output The folder of the report.
#' @param rows Rows of the head and of the tail. `0` prints no rows.
#' @param render Render the report to one HTML file when Quarto is on the
#'   `PATH`.
#' @return The path of the HTML file, or of the `.qmd` file when no HTML was
#'   rendered, invisibly.
#' @export
#' @examples
#' if (sdg_available()) {
#'   path <- sdg_quarto(airquality, render = FALSE)
#'   readLines(path, n = 12)
#' }
sdg_quarto <- function(data, synthetic = NULL, output = tempfile("sdg_quarto_"), rows = 5L, render = TRUE,
                       name = "dataframe.csv", private = NULL, drop = NULL, flags = character()) {
  folder <- tempfile("sdgr_")
  on.exit(unlink(folder, recursive = TRUE), add = TRUE)
  args <- c("quarto", data_targets(data, file.path(folder, "data"), name), "-o", path.expand(output),
            "--rows", format(rows, scientific = FALSE))
  if (!is.null(synthetic)) {
    args <- c(args, "--synthetic", data_targets(synthetic, file.path(folder, "synthetic"), name))
  }
  if (!isTRUE(render)) {
    args <- c(args, "--no-render")
  }
  args <- c(args, reading_args(private, FALSE, drop, NULL, flags))
  dir.create(output, recursive = TRUE, showWarnings = FALSE)
  before <- list.files(output, full.names = TRUE)
  sdg_run(args)
  written <- setdiff(list.files(output, full.names = TRUE), before)
  html <- written[grepl("\\.html$", written)]
  invisible(if (length(html)) html[[1]] else written[grepl("\\.qmd$", written)][[1]])
}

#' Print the text of sdg
#'
#' @param x The lines, with the class `sdg_text`.
#' @param ... Not used.
#' @return `x`, invisibly.
#' @export
print.sdg_text <- function(x, ...) {
  writeLines(unclass(x))
  invisible(x)
}

#' Give the files that sdg reads
#'
#' @param data A data frame, or the paths of files and folders.
#' @param folder The temporary folder for a data frame.
#' @param name File name of a data frame.
#' @return The paths for sdg.
#' @noRd
data_targets <- function(data, folder, name) {
  if (is.data.frame(data)) {
    dir.create(folder, recursive = TRUE, showWarnings = FALSE)
    path <- file.path(folder, basename(name))
    write_frame(data, path)
    return(path)
  }
  if (!is.character(data) || !length(data)) {
    stop("Give a data frame, or the paths of files and folders.", call. = FALSE)
  }
  missing <- data[!file.exists(data)]
  if (length(missing)) {
    stop("No file or folder: ", paste(missing, collapse = ", "), call. = FALSE)
  }
  path.expand(data)
}

#' Write a data frame as the file type of its name
#'
#' @param frame The data frame.
#' @param path Path of the file. The suffix is `.csv`, `.tsv` or `.rds`.
#' @return `NULL`, invisibly.
#' @noRd
write_frame <- function(frame, path) {
  suffix <- tolower(tools::file_ext(path))
  if (suffix == "csv") {
    utils::write.csv(frame, path, row.names = FALSE)
  } else if (suffix == "tsv") {
    utils::write.table(frame, path, sep = "\t", row.names = FALSE, quote = FALSE)
  } else if (suffix == "rds") {
    saveRDS(frame, path)
  } else {
    stop("Give a name with the suffix .csv, .tsv or .rds, not '", basename(path), "'.", call. = FALSE)
  }
  invisible(NULL)
}

#' Give the reading options of sdg
#'
#' @inheritParams sdg_glimpse
#' @return The arguments for sdg.
#' @noRd
reading_args <- function(private, generic_labels, drop, label_key, flags) {
  args <- character(0)
  if (!is.null(private)) {
    args <- c(args, "--private", private)
  }
  if (isTRUE(generic_labels)) {
    args <- c(args, "--generic-labels")
  }
  if (!is.null(drop)) {
    args <- c(args, "--drop", drop)
  }
  if (!is.null(label_key)) {
    args <- c(args, "--label-key", path.expand(label_key))
  }
  c(args, as.character(flags))
}
