test_that("the example config makes the visits table", {
  skip_if_not(sdg_available(), "sdg does not run")
  config <- system.file("extdata", "visits.yaml", package = "sdgr")
  expect_match(paste(sdg_check(config), collapse = "\n"), "Valid config")
  tables <- sdg_data(config)
  expect_identical(names(tables), "visits.csv")
  visits <- tables[["visits.csv"]]
  expect_identical(names(visits), c("visit_id", "arm", "il6", "crp", "visit_date", "fasting"))
  expect_identical(nrow(visits), 80L)
  expect_true(is.numeric(visits$il6) && is.logical(visits$fasting))
  expect_gt(stats::cor(visits$il6, visits$crp, method = "spearman", use = "complete.obs"), 0.5)
})

test_that("a bad config stops with the issues of sdg", {
  skip_if_not(sdg_available(), "sdg does not run")
  config <- tempfile(fileext = ".yaml")
  on.exit(unlink(config), add = TRUE)
  writeLines(c("version: 1", "seed: 1", "datasets: []"), config)
  expect_error(sdg_check(config), "Invalid config")
})

test_that("a data frame gives a glimpse, a report and a config with no real value", {
  skip_if_not(sdg_available(), "sdg does not run")
  visits <- invented_visits()
  glimpse <- sdg_glimpse(visits)
  expect_s3_class(glimpse, "sdg_text")
  text <- paste(glimpse, collapse = "\n")
  expect_match(text, "'record_id' <identifier>", fixed = TRUE)
  expect_false(grepl("R1005", text, fixed = TRUE))
  expect_output(print(glimpse), "sdg glimpse")
  report <- sdg_report(visits)
  expect_true(file.exists(report))
  expect_false(any(grepl("R1005", readLines(report), fixed = TRUE)))
  config <- sdg_profile(visits, seed = 7)
  expect_false(any(grepl("R1005", readLines(config), fixed = TRUE)))
  synthetic <- sdg_data(config)[[1]]
  expect_identical(names(synthetic), names(visits))
  expect_identical(nrow(synthetic), nrow(visits))
  expect_identical(sum(is.na(synthetic$crp)), sum(is.na(visits$crp)))
  expect_length(intersect(synthetic$record_id, visits$record_id), 0L)
})

test_that("a Quarto report puts the real and the synthetic rows side by side", {
  skip_if_not(sdg_available(), "sdg does not run")
  visits <- invented_visits()
  synthetic <- sdg_data(sdg_profile(visits, seed = 3))[[1]]
  expect_message(path <- sdg_quarto(visits, synthetic = synthetic, render = FALSE), "secure machine")
  text <- readLines(path)
  expect_true(any(grepl("sdg-secure-only", text, fixed = TRUE)))
  expect_true(any(grepl("### Synthetic rows", text, fixed = TRUE)))
  expect_true(any(grepl("R1000", text, fixed = TRUE)))
})

test_that("generic labels hide the column names", {
  skip_if_not(sdg_available(), "sdg does not run")
  key <- tempfile(fileext = ".csv")
  on.exit(unlink(key), add = TRUE)
  text <- paste(sdg_glimpse(invented_visits(), generic_labels = TRUE, label_key = key), collapse = "\n")
  expect_false(grepl("record_id", text, fixed = TRUE))
  expect_match(paste(readLines(key), collapse = "\n"), "record_id", fixed = TRUE)
})

test_that("a data frame as RDS gives an RDS file that R reads", {
  skip_if_not(sdg_available(), "sdg does not run")
  config <- tryCatch(sdg_profile(invented_visits(), name = "visits.rds"), error = skip_without_r_extra)
  output <- sdg_generate(config)
  expect_true(any(grepl("\\.rds$", list.files(output))))
  synthetic <- sdg_read(output)[[1]]
  expect_identical(nrow(synthetic), 60L)
  expect_true(is.numeric(synthetic$crp))
})
