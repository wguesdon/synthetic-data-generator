#' The sdg command that sdgr runs
#'
#' sdgr calls the `sdg` command line tool. The command comes from the option
#' `sdgr.command`, then from the environment variable `SDGR_COMMAND`, then
#' from the program `sdg` on the `PATH`.
#'
#' @return A character vector: the program, then its first arguments.
#' @export
#' @examples
#' old <- options(sdgr.command = c("uv", "run", "--project", "~/synthetic-data-generator", "sdg"))
#' sdg_command()
#' options(old)
sdg_command <- function() {
  command <- getOption("sdgr.command")
  if (!is.null(command)) {
    return(as.character(command))
  }
  variable <- trimws(Sys.getenv("SDGR_COMMAND"))
  if (nzchar(variable)) {
    return(strsplit(variable, "[[:space:]]+")[[1]])
  }
  "sdg"
}

#' Run one sdg command
#'
#' The function stops with the output of sdg when sdg fails. When sdg
#' succeeds, the lines that sdg writes to the standard error become a message.
#'
#' @param args The arguments after `sdg`, such as `c("check", "config.yaml")`.
#' @return The lines of the standard output, invisibly.
#' @export
#' @examples
#' if (sdg_available()) {
#'   sdg_run("--version")
#' }
sdg_run <- function(args) {
  command <- sdg_command()
  if (!nzchar(Sys.which(command[[1]]))) {
    stop("The program '", command[[1]], "' is not on the PATH. Install sdg, or set ",
         "options(sdgr.command = ...) or the environment variable SDGR_COMMAND.", call. = FALSE)
  }
  errors <- tempfile("sdgr_stderr_")
  on.exit(unlink(errors), add = TRUE)
  output <- suppressWarnings(system2(command[[1]], shQuote(c(command[-1], args)), stdout = TRUE,
                                     stderr = errors))
  status <- attr(output, "status")
  notes <- if (file.exists(errors)) readLines(errors, warn = FALSE) else character(0)
  if (!is.null(status) && status != 0L) {
    stop("sdg ", args[[1]], " stopped with status ", status, ":\n",
         paste(c(output, notes), collapse = "\n"), call. = FALSE)
  }
  if (length(notes)) {
    message(paste(notes, collapse = "\n"))
  }
  invisible(as.character(output))
}

#' The version of sdg
#'
#' @return The version, such as `"0.13.0"`.
#' @export
#' @examples
#' if (sdg_available()) {
#'   sdg_version()
#' }
sdg_version <- function() {
  sub("^sdg[[:space:]]+", "", sdg_run("--version")[[1]])
}

#' Check that sdgr can run sdg
#'
#' @return `TRUE` when `sdg --version` runs, otherwise `FALSE`.
#' @export
#' @examples
#' sdg_available()
sdg_available <- function() {
  !inherits(try(sdg_version(), silent = TRUE), "try-error")
}
