#' sdgr: synthetic datasets from a YAML config with sdg
#'
#' sdgr calls the `sdg` command line tool from R. On the secure machine,
#' [sdg_glimpse()], [sdg_report()] and [sdg_profile()] describe a data frame
#' with no real value, and [sdg_quarto()] shows the real data beside the
#' synthetic data in a report that stays on that machine. On any machine,
#' [sdg_check()], [sdg_generate()], [sdg_read()] and [sdg_data()] make
#' synthetic data from a config and read it back as data frames.
#' [sdg_command()] tells how sdgr finds sdg.
#'
#' @keywords internal
"_PACKAGE"
