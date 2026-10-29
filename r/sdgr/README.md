# sdgr: sdg from R

`sdgr` is an R package that calls the `sdg` command line tool. The package writes a config from a data frame, checks a config, generates synthetic data and reads the synthetic tables back as data frames. The package has no dependency outside base R. The package `arrow` reads a Parquet output, and the package `readxl` reads an Excel output.

## Install

`sdgr` needs the `sdg` command. Install `sdg` as a uv tool, so that the command `sdg` is on the `PATH`. The extra `sdg[r]` adds the RDS format.

```bash
uv tool install "sdg[r] @ git+https://github.com/wguesdon/synthetic-data-generator@v0.14.0"
```

Then install the R package from GitHub with the package `remotes`.

```r
remotes::install_github("wguesdon/synthetic-data-generator", subdir = "r/sdgr", ref = "v0.14.0")
```

A clone of the repository works too.

```bash
git clone --branch v0.14.0 https://github.com/wguesdon/synthetic-data-generator.git
R CMD INSTALL synthetic-data-generator/r/sdgr
```

If `sdg` is not on the `PATH`, tell `sdgr` the command. The option `sdgr.command` comes first, then the environment variable `SDGR_COMMAND`.

```r
options(sdgr.command = c("uv", "run", "--project", "~/synthetic-data-generator", "sdg"))
sdgr::sdg_available()
```

## Functions

| Function | Machine | Result |
|---|---|---|
| `sdg_glimpse(data)` | Secure | The glimpse report of a data frame or of files, with no real value |
| `sdg_report(data)` | Secure | The path of the audited report: the glimpse, the designs and the group differences |
| `sdg_profile(data)` | Secure | The path of a config made from a data frame or from files |
| `sdg_check(config)` | Any | The summary of a valid config. The function stops with each issue of a config that is not valid |
| `sdg_generate(config)` | Any | The output folder with the synthetic data |
| `sdg_read(output)` | Any | A named list of the data frames in an output folder |
| `sdg_data(config)` | Any | The data frames of a new output in a temporary folder |
| `sdg_run(args)` | Any | The output of any other `sdg` command |

A data frame goes to a temporary CSV file, and the function deletes the file before it returns. The argument `name` sets the file name, such as `name = "visits.rds"` for an RDS file. A name that matches the private pattern makes the table private, and the argument `private` changes the pattern.

## Example

On the secure machine, make the report and read it before it leaves the machine.

```r
library(sdgr)
sdg_glimpse(visits)
config <- sdg_profile(visits, output = "visits.yaml")
```

On the workstation, make the synthetic data from the config.

```r
tables <- sdg_data("visits.yaml")
str(tables[["file_1.csv"]])
```

The package holds one invented config. `sdg_data(system.file("extdata", "visits.yaml", package = "sdgr"))` makes its table.

## Tests

`scripts/test_r_package.sh` runs `R CMD check` on the package in a `rocker/r-ver` container with Podman. Run the script from the repository root. The container uses the `uv` of the host and writes the `sdg` environment under `sdg_output/r_check/`.

## License

The package `sdgr` has the MIT license. `LICENSE.md` gives the text.
