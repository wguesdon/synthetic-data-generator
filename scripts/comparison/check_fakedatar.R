# Run the capability checks of capabilities.txt on FakeDataR 0.2.2.
#
# Each check runs FakeDataR on the invented input of make_capability_input.py
# and returns pass or fail with a note. A check passes when any documented
# setting of FakeDataR passes it, such as one of the three privacy levels. A
# capability that FakeDataR has no function for gets no check, and the table
# marks it none.
#
# Usage: Rscript check_fakedatar.R LIBRARY INPUT_DIR OUT_DIR
# Writes OUT_DIR/fakedatar.tsv.

args <- commandArgs(trailingOnly = TRUE)
.libPaths(c(args[[1]], .libPaths()))
input_dir <- args[[2]]
out_dir <- args[[3]]
seed <- 42
email_pattern <- "^[^@[:space:]]+@[^@[:space:]]+\\.[a-z]{2,}$"

suppressPackageStartupMessages(library(FakeDataR))

#' Read an invented CSV file with text kept as text.
read_input <- function(name) {
  utils::read.csv(file.path(input_dir, name), stringsAsFactors = FALSE, na.strings = c("", "NA"),
                  check.names = FALSE)
}

#' Turn the ISO date columns of the visits table into Date columns, as readr does.
with_dates <- function(table) {
  for (name in c("visit_date", "admit_date", "discharge_date")) {
    table[[name]] <- as.Date(table[[name]])
  }
  table
}

#' Return a check result.
result <- function(passed, note) {
  list(passed = isTRUE(passed), note = note)
}

#' Turn an error of a check into a failed result.
failed_check <- function(error) {
  result(FALSE, paste("error:", conditionMessage(error)))
}

#' Return the shape of each text: a letter becomes A and a digit becomes 9.
shapes <- function(values) {
  gsub("[0-9]", "9", gsub("[A-Za-z]", "A", values))
}

#' Write a level of llm_bundle into a folder and return its files as one text.
bundle_text <- function(table, level, folder) {
  dir.create(folder, showWarnings = FALSE, recursive = TRUE)
  bundle <- llm_bundle(table, n = nrow(table), level = level, path = folder, filename = "fake_bundle",
                       seed = seed)
  fake <- readRDS(bundle$data_paths$rds)
  extra <- attributes(fake)[setdiff(names(attributes(fake)), c("names", "row.names", "class"))]
  texts <- c(readLines(bundle$data_paths$csv), readLines(bundle$schema_path), readLines(bundle$readme_path),
             utils::capture.output(dput(extra)))
  paste(texts, collapse = "\n")
}

check_i1 <- function() {
  visits <- read_input("visits.csv")
  fake <- generate_fake_data(visits, n = nrow(visits), seed = seed)
  result(identical(names(fake), names(visits)), paste(ncol(fake), "columns copied"))
}

check_i2 <- function() {
  workbook <- list(visits = read_input("visits.csv")[, c("patient_id", "arm", "crp")],
                   labs = read_input("visits.csv")[, c("patient_id", "il6", "ward")])
  fake <- generate_fake_data(workbook, n = 50, seed = seed)
  two <- is.list(fake) && !is.data.frame(fake) && length(fake) == 2
  result(two, paste("a list of two tables gives one", class(fake)[1], "with", ncol(fake), "columns"))
}

check_i5 <- function() {
  visits <- read_input("visits.csv")
  fake <- generate_fake_data(visits, n = 50, seed = seed)
  result(is.data.frame(fake) && identical(names(fake), names(visits)), "generate_fake_data(data frame)")
}

check_i6 <- function() {
  connection <- DBI::dbConnect(RSQLite::SQLite(), file.path(input_dir, "schema.sqlite"))
  on.exit(DBI::dbDisconnect(connection), add = TRUE)
  schema <- schema_from_db(connection, "visits")
  fake <- generate_fake_from_schema(schema, n = 50, seed = seed)
  expected <- c("patient_id", "age", "crp", "visit_date", "fasting")
  result(identical(names(fake), expected) && nrow(fake) == 50, "schema_from_db and generate_fake_from_schema")
}

check_v1 <- function() {
  real <- read_input("visits.csv")$crp
  real <- real[!is.na(real)]
  notes <- character(0)
  passed <- FALSE
  for (mode in c("range", "distribution")) {
    fake <- generate_fake_data(read_input("visits.csv"), n = 200, numeric_mode = mode, seed = seed)$crp
    fake <- fake[!is.na(fake)]
    ks <- suppressWarnings(stats::ks.test(real, fake)$statistic)
    copied <- mean(fake %in% real)
    passed <- passed || (ks < 0.1 && copied < 0.05)
    notes <- c(notes, sprintf("%s: KS %.3f, copied %.3f", mode, ks, copied))
  }
  result(passed, paste(notes, collapse = "; "))
}

check_v2 <- function() {
  real <- prop.table(table(read_input("visits.csv")$arm))
  fake <- generate_fake_data(read_input("visits.csv"), n = 1000, category_mode = "preserve", seed = seed)
  shares <- prop.table(table(factor(fake$arm, levels = names(real))))
  gap <- max(abs(as.numeric(real) - as.numeric(shares)))
  result(gap <= 0.05, sprintf("largest share gap %.3f", gap))
}

check_v3 <- function() {
  counts <- integer(0)
  for (run in 0:9) {
    fake <- generate_fake_data(read_input("visits.csv"), n = 200, seed = run)
    counts <- c(counts, sum(is.na(fake$crp)))
  }
  result(all(abs(counts - 20) <= 1), paste("missing counts", paste(sort(unique(counts)), collapse = ", "),
                                           "real 20"))
}

check_v4 <- function() {
  fake <- generate_fake_data(read_input("visits.csv"), n = 200, category_mode = "preserve", seed = seed)$il6
  share <- mean(fake == "<LOD", na.rm = TRUE)
  numbers <- all(!is.na(suppressWarnings(as.numeric(fake[fake != "<LOD"]))))
  result(abs(share - 0.15) <= 0.02 && numbers, sprintf("<LOD share %.3f, the other cells numbers %s", share,
                                                       numbers))
}

check_v5 <- function() {
  real <- read_input("visits.csv")$patient_id
  notes <- character(0)
  passed <- FALSE
  for (level in c("low", "medium", "high")) {
    fake <- generate_fake_with_privacy(read_input("visits.csv"), n = 200, level = level, seed = seed)[[1]]
    shape_ok <- all(shapes(fake) == "A9999")
    copied <- sum(fake %in% real)
    passed <- passed || (shape_ok && copied <= 2)
    notes <- c(notes, sprintf("%s: shape %s, copied %d", level, shape_ok, copied))
  }
  result(passed, paste(notes, collapse = "; "))
}

check_v7 <- function() {
  real <- with_dates(read_input("visits.csv"))$visit_date
  span <- as.numeric(max(real) - min(real))
  notes <- character(0)
  passed <- FALSE
  for (mode in c("range", "distribution")) {
    fake <- generate_fake_data(with_dates(read_input("visits.csv")), n = 200, numeric_mode = mode,
                               seed = seed)$visit_date
    drawn <- as.numeric(max(fake, na.rm = TRUE) - min(fake, na.rm = TRUE))
    copied <- sum(fake %in% real)
    passed <- passed || (abs(drawn - span) <= 0.1 * span && copied == 0)
    notes <- c(notes, sprintf("%s: span %.0f, copied %d", mode, drawn, copied))
  }
  result(passed, paste(notes, collapse = "; "))
}

check_v8 <- function() {
  real <- mean(read_input("visits.csv")$fasting)
  fake <- generate_fake_data(read_input("visits.csv"), n = 200, seed = seed)$fasting
  share <- mean(fake, na.rm = TRUE)
  result(abs(share - real) <= 0.05, sprintf("true share %.3f, real %.3f", share, real))
}

check_v9 <- function() {
  fake <- generate_fake_data(read_input("visits.csv"), n = 200, category_mode = "preserve", seed = seed)$ward
  result(all(shapes(fake) == "A99"), "the low level samples the real codes")
}

check_v10 <- function() {
  people <- data.frame(name = sprintf("Person Number%d", 1:1000), stringsAsFactors = FALSE)
  fake <- generate_fake_with_privacy(people, n = 1000, level = "low", seed = seed)$name
  full <- unique(fake[grepl(" ", fake)])
  result(length(full) >= 500, sprintf("%d distinct full names; %d distinct values", length(full),
                                      length(unique(fake))))
}

check_v11 <- function() {
  contacts <- data.frame(email = read_input("visits.csv")$contact, stringsAsFactors = FALSE)
  fake <- generate_fake_with_privacy(contacts, n = 1000, level = "low", seed = seed)$email
  valid <- sum(grepl(email_pattern, fake))
  result(valid == 1000, sprintf("%d valid of 1000", valid))
}

check_v12 <- function() {
  phones <- data.frame(phone = sprintf("555-01%02d", 1:200), stringsAsFactors = FALSE)
  fake <- generate_fake_with_privacy(phones, n = 1000, level = "low", seed = seed)$phone
  valid <- sum(nchar(gsub("[^0-9]", "", fake)) >= 10)
  result(valid == 1000, sprintf("%d of 1000 with 10 digits or more", valid))
}

check_v13 <- function() {
  places <- data.frame(address = sprintf("%d Main Street", 1:200), stringsAsFactors = FALSE)
  fake <- generate_fake_with_privacy(places, n = 1000, level = "low", seed = seed)$address
  valid <- sum(grepl("[0-9]", fake) & lengths(strsplit(fake, " ")) >= 3)
  result(valid == 1000, sprintf("%d of 1000; example %s", valid, fake[[1]]))
}

check_v16 <- function() {
  first <- generate_fake_data(read_input("visits.csv"), n = 100, seed = seed)
  second <- generate_fake_data(read_input("visits.csv"), n = 100, seed = seed)
  result(identical(first, second), "identical data frames")
}

check_v17 <- function() {
  fake <- generate_fake_data(read_input("visits.csv"), n = 1000, seed = seed)
  result(nrow(fake) == 1000, paste(nrow(fake), "rows"))
}

check_d5 <- function() {
  real <- stats::cor(read_input("visits.csv")$x, read_input("visits.csv")$y, method = "spearman")
  notes <- character(0)
  passed <- FALSE
  for (mode in c("range", "distribution")) {
    fake <- generate_fake_data(read_input("visits.csv"), n = 200, numeric_mode = mode, seed = seed)
    rho <- stats::cor(fake$x, fake$y, method = "spearman")
    passed <- passed || rho > 0.5
    notes <- c(notes, sprintf("%s: rho %.2f", mode, rho))
  }
  result(passed, paste(c(notes, sprintf("real %.2f", real)), collapse = "; "))
}

check_d6 <- function() {
  fake <- generate_fake_data(with_dates(read_input("visits.csv")), n = 200, seed = seed)
  ordered <- mean(fake$discharge_date >= fake$admit_date, na.rm = TRUE)
  result(ordered == 1, sprintf("ordered share %.2f", ordered))
}

check_d7 <- function() {
  patients <- generate_fake_data(read_input("patients.csv"), n = 80, category_mode = "preserve", seed = seed)
  admissions <- generate_fake_data(read_input("admissions.csv"), n = 240, category_mode = "preserve", seed = seed)
  found <- mean(admissions$patient_id %in% patients$patient_id)
  result(found == 1, sprintf("child keys found %.2f", found))
}

check_p1 <- function() {
  flagged <- detect_sensitive_columns(names(read_input("demographics.csv")))
  result(all(c("age", "sex") %in% flagged), paste("flagged:", paste(flagged, collapse = ", ")))
}

check_p2 <- function() {
  real <- read_input("visits.csv")$contact
  text <- bundle_text(read_input("visits.csv"), "medium", file.path(out_dir, "fakedatar_p2"))
  leaked <- sum(vapply(real, grepl, logical(1), x = text, fixed = TRUE))
  result(leaked == 0, sprintf("%d real emails in the medium bundle", leaked))
}

check_p3 <- function() {
  visits <- read_input("visits.csv")
  fake <- generate_fake_with_privacy(visits, n = 200, level = "medium", seed = seed)
  names_left <- intersect(names(visits), names(fake))
  levels_left <- intersect(c("placebo", "drug_a", "drug_b"), unlist(fake, use.names = FALSE))
  result(length(names_left) == 0 && length(levels_left) == 0,
         sprintf("real names %d, real levels %d in the medium data", length(names_left), length(levels_left)))
}

check_p4 <- function() {
  visits <- read_input("visits.csv")
  text <- bundle_text(visits, "medium", file.path(out_dir, "fakedatar_p4"))
  found <- names(visits)[vapply(sprintf("\\b%s\\b", names(visits)), grepl, logical(1), x = text, perl = TRUE)]
  result(length(found) == 0, paste("real names in the bundle:", paste(head(found, 4), collapse = ", ")))
}

check_p7 <- function() {
  demographics <- read_input("demographics.csv")
  bundle <- llm_bundle(demographics, n = nrow(demographics), level = "medium",
                       path = file.path(out_dir, "fakedatar_p7"), filename = "fake_bundle", seed = seed)
  schema <- jsonlite::fromJSON(bundle$schema_path, simplifyVector = FALSE)
  age <- schema$columns[[2]]
  shown <- c(age$min, age$max) %in% range(demographics$age)
  result(!any(shown), sprintf("schema age min %s and max %s, real %s to %s", age$min, age$max,
                              min(demographics$age), max(demographics$age)))
}

check_p8 <- function() {
  fake <- generate_fake_with_privacy(read_input("visits.csv"), n = 50, level = "low", sensitive = "contact",
                                     sensitive_strategy = "drop", seed = seed)
  result(!("contact" %in% names(fake)), "sensitive_strategy = \"drop\"")
}

check_p9 <- function() {
  real <- read_input("visits.csv")$contact
  contacts <- data.frame(email = real, stringsAsFactors = FALSE)
  fake <- generate_fake_with_privacy(contacts, n = 200, level = "low", seed = seed)$email
  result(all(grepl(email_pattern, fake)) && !any(fake %in% real), "sensitive_strategy = \"fake\"")
}

check_o1 <- function() {
  path <- file.path(out_dir, "fakedatar_o1.csv")
  export_fake(generate_fake_data(read_input("visits.csv"), n = 20, seed = seed), path)
  result(nrow(utils::read.csv(path)) == 20, "export_fake to CSV")
}

check_o2 <- function() {
  path <- file.path(out_dir, "fakedatar_o2.xlsx")
  export_fake(generate_fake_data(read_input("visits.csv"), n = 20, seed = seed), path)
  result(file.exists(path), "export_fake to xlsx")
}

check_o3 <- function() {
  path <- file.path(out_dir, "fakedatar_o3.parquet")
  export_fake(generate_fake_data(read_input("visits.csv"), n = 20, seed = seed), path)
  result(nrow(arrow::read_parquet(path)) == 20, "export_fake to Parquet")
}

check_o4 <- function() {
  path <- file.path(out_dir, "fakedatar_o4.rds")
  export_fake(generate_fake_data(read_input("visits.csv"), n = 20, seed = seed), path)
  result(nrow(readRDS(path)) == 20, "export_fake to RDS")
}

check_o5 <- function() {
  bundle <- llm_bundle(read_input("visits.csv"), n = 20, path = file.path(out_dir, "fakedatar_o5"),
                       filename = "fake_bundle", seed = seed)
  ok <- file.exists(bundle$data_paths$csv) && file.exists(bundle$schema_path) && file.exists(bundle$readme_path)
  result(ok, "llm_bundle: CSV, RDS, JSON schema and README_FOR_LLM.txt")
}

check_o6 <- function() {
  visits <- read_input("visits.csv")
  fake <- generate_fake_data(visits, n = 200, seed = seed)
  fake$crp <- as.character(fake$crp)
  report <- validate_fake(visits, fake)
  result(!report$class_match[report$column == "crp"], "validate_fake flags the class of crp")
}

check_w3 <- function() {
  fake <- generate_fake_data(read_input("visits.csv"), n = 10, seed = seed)
  result(nrow(fake) == 10, "generate_fake_data in R")
}

check_w8 <- function() {
  connection <- DBI::dbConnect(RSQLite::SQLite(), file.path(input_dir, "schema.sqlite"))
  on.exit(DBI::dbDisconnect(connection), add = TRUE)
  path <- file.path(out_dir, "fakedatar_schema.csv")
  utils::write.csv(schema_from_db(connection, "visits"), path, row.names = FALSE)
  first <- generate_fake_from_schema(utils::read.csv(path, stringsAsFactors = FALSE), n = 50, seed = seed)
  second <- generate_fake_from_schema(utils::read.csv(path, stringsAsFactors = FALSE), n = 50, seed = seed)
  result(identical(first, second), "a schema data frame saved as CSV")
}

check_w9 <- function() {
  table <- prepare_input_data(read_input("visits.csv")[, "score_pct", drop = FALSE])
  result(is.numeric(table$score_pct), paste("score_pct class", class(table$score_pct)[1]))
}

checks <- list(I1 = check_i1, I2 = check_i2, I5 = check_i5, I6 = check_i6, V1 = check_v1, V2 = check_v2,
               V3 = check_v3, V4 = check_v4, V5 = check_v5, V7 = check_v7, V8 = check_v8, V9 = check_v9,
               V10 = check_v10, V11 = check_v11, V12 = check_v12, V13 = check_v13, V16 = check_v16,
               V17 = check_v17, D5 = check_d5, D6 = check_d6, D7 = check_d7, P1 = check_p1, P2 = check_p2,
               P3 = check_p3, P4 = check_p4, P7 = check_p7, P8 = check_p8, P9 = check_p9, O1 = check_o1,
               O2 = check_o2, O3 = check_o3, O4 = check_o4, O5 = check_o5, O6 = check_o6, W3 = check_w3,
               W8 = check_w8, W9 = check_w9)

dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
rows <- data.frame(id = character(0), result = character(0), note = character(0), stringsAsFactors = FALSE)
for (id in names(checks)) {
  outcome <- tryCatch(checks[[id]](), error = failed_check)
  status <- if (outcome$passed) "pass" else "fail"
  note <- gsub("[\t\n]", " ", outcome$note)
  cat(sprintf("fakedatar %s: %s (%s)\n", id, status, note))
  rows[nrow(rows) + 1, ] <- c(id, status, note)
}
utils::write.table(rows, file.path(out_dir, "fakedatar.tsv"), sep = "\t", row.names = FALSE, quote = FALSE)
