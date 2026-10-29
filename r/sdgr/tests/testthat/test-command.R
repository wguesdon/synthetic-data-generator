test_that("the option sdgr.command comes first", {
  old <- options(sdgr.command = c("uv", "run", "sdg"))
  on.exit(options(old), add = TRUE)
  expect_identical(sdg_command(), c("uv", "run", "sdg"))
})

test_that("the environment variable SDGR_COMMAND splits on spaces", {
  old <- options(sdgr.command = NULL)
  on.exit(options(old), add = TRUE)
  previous <- Sys.getenv("SDGR_COMMAND", unset = NA)
  on.exit(if (is.na(previous)) Sys.unsetenv("SDGR_COMMAND") else Sys.setenv(SDGR_COMMAND = previous), add = TRUE)
  Sys.setenv(SDGR_COMMAND = " uv  run --quiet sdg ")
  expect_identical(sdg_command(), c("uv", "run", "--quiet", "sdg"))
})

test_that("a missing program stops with a clear message", {
  old <- options(sdgr.command = "sdgr_no_such_program")
  on.exit(options(old), add = TRUE)
  expect_error(sdg_run("--version"), "'sdgr_no_such_program' is not on the PATH")
  expect_false(sdg_available())
})

test_that("a data frame needs a name with a known suffix", {
  expect_error(sdgr:::data_targets(invented_visits(), tempfile(), "visits.xlsx"), "suffix .csv, .tsv or .rds")
  expect_error(sdgr:::data_targets(42, tempfile(), "visits.csv"), "Give a data frame")
  expect_error(sdgr:::data_targets("no_such_file.csv", tempfile(), "visits.csv"), "No file or folder")
})

test_that("the separator comes from the header line", {
  path <- tempfile(fileext = ".csv")
  on.exit(unlink(path), add = TRUE)
  writeLines(c("a;b;c", "1;2;3"), path)
  expect_identical(sdgr:::guess_separator(path), ";")
})
