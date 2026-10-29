# Invented data for the tests. No value comes from a real person.
invented_visits <- function(rows = 60L) {
  index <- seq_len(rows) - 1L
  data.frame(
    record_id = sprintf("R%04d", 1000L + index),
    arm = factor(ifelse(index %% 3L == 0L, "active", "placebo")),
    crp = ifelse(index %% 10L == 0L, NA, 1.5 + index * 0.25),
    visit_date = as.Date("2024-01-01") + index * 7L,
    fasting = index %% 4L == 0L,
    stringsAsFactors = FALSE
  )
}

# Skip a test that needs the extra sdg[r], and stop on any other error.
skip_without_r_extra <- function(error) {
  if (grepl("sdg[r]", conditionMessage(error), fixed = TRUE)) {
    testthat::skip("sdg has no extra sdg[r]")
  }
  stop(error)
}
