# Repository instructions

This file is the one source of the repository instructions for each agent harness. `CLAUDE.md` is a copy of this file, because Claude Code reads `CLAUDE.md`. Codex, opencode and pi read `AGENTS.md`.

This repository holds `sdg`, a tool that makes synthetic datasets from a YAML config. With the synthetic data, an agent can help with the code of a customer project while the real data stays on a secure machine.

## Data boundary

These rules apply to each session in this repository.

- Do not open, list or read real data.
- Use only the description and the glimpse reports that the user pastes as sources about the real data.
- Do not infer or reconstruct a real value from a report.
- If a report shows a real identifier or a real measurement, stop and tell the user. Do not use that value.

## Requests for synthetic data

For a request for synthetic data, read and follow the operating guide for your harness before you work. The guide defines the workflow, and it requires the `uv run sdg` command surface.

| Harness | Operating guide | How the harness loads it |
|---|---|---|
| Claude Code | `skills/claude-code/SKILL.md` | The project skill `.claude/skills/sdg/SKILL.md`. Invoke it with `/sdg` |
| Codex | `skills/codex/AGENTS.md` | Codex reads this file. It also finds the skill `.agents/skills/sdg/SKILL.md` |
| opencode | `skills/opencode/AGENTS.md` | The `instructions` list in `opencode.json`, the skill in `.agents/skills/`, and the `/sdg` command |
| pi | `skills/claude-code/SKILL.md` | The skill `.agents/skills/sdg/SKILL.md`. Invoke it with `/skill:sdg`, or use the `/sdg` prompt template |

The files under `.claude/`, `.agents/`, `.opencode/` and `.pi/`, and the two adapters `skills/codex/AGENTS.md` and `skills/opencode/AGENTS.md`, are written by `scripts/sync_agent_harness.sh`. The repository has no symlinks, because some container setups stop when a mounted tree holds a symlink.

For work on the repository itself, follow the rules below.

## Repository layout

| Path | Contents |
|---|---|
| `src/sdg/` | The Python package. `cli.py` is the command surface |
| `src/sdg/formats/` | One module per format. `__init__.py` lists the modules |
| `src/sdg/privacy.py` | The privacy rules of the glimpse, the profile and the audit |
| `tests/` | The `pytest` tests |
| `tests/smoke_test.sh` | The end-to-end test over each example |
| `skills/claude-code/SKILL.md` | The skill and the one source of the operating guide |
| `skills/opencode/command/sdg.md`, `skills/pi/prompts/sdg.md` | The opencode command and the pi prompt template |
| `scripts/sync_agent_harness.sh` | Writes the copies and the adapters. With `--check`, it fails when a file differs from its source |
| `single_script/sdg_secure.py` | The whole package in one file, for a machine where `sdg` is not installed |
| `scripts/build_single_script.py` | Writes the single script from `src/sdg`. With `--check`, it fails when the script differs from the source |
| `r/sdgr/` | The R package that calls `sdg`. `scripts/test_r_package.sh` runs `R CMD check` on it in a container |
| `docs/how_it_works.md` | The full config reference and one example for each format |
| `example/` | Generic descriptions and the configs made from them |

## Rules for work in this repository

1. Use Python 3.13 and `uv`. Add a dependency with `uv add`. Run code with `uv run`. Do not use `pip`.
2. Write Google style docstrings. Do not define a function inside another function. Write the helper at module level.
3. Run `uv run pytest` before each commit. Run `tests/smoke_test.sh` after a change to a format module or to the CLI. Run `scripts/test_r_package.sh` after a change to `r/sdgr/` or to the CLI.
4. Give each privacy rule a test. Do not change a privacy rule without a test that shows the new behavior.
5. Edit only the sources in `skills/` and this file. Then run `scripts/sync_agent_harness.sh` to write the copies, and commit the sources and the copies together. Run `scripts/sync_agent_harness.sh --check` before a commit. Do not add a symlink.
6. A new format adds one module to `src/sdg/formats/`, one entry in `MODULES`, a row in the format table of the skill, a section in `docs/how_it_works.md`, an example in `example/` and a case in the smoke test.
7. Run every command from the repository root. Put a copy of each input from the user in `input/<project>/`. Write every output under `sdg_output/<project>/`. Git ignores both folders. Do not write a file outside the repository.
8. Do not commit real data. Git ignores `input/`, `sdg_output/`, `data/` and the common data file extensions, except under `example/`.
9. Record each change that a user can see in `CHANGELOG.md`. Change `VERSION`, `src/sdg/version.py` and the `Version` of `r/sdgr/DESCRIPTION` together.
10. After a change to `src/sdg/`, run `uv run python scripts/build_single_script.py`. Commit the single script with the change. `uv run pytest` fails when the single script differs from the source.

## Commits

- Use the Conventional Commits style, for example `feat:`, `fix:` or `docs:`.
- Do not mention Claude, an agent or a model in a commit message. Do not add a `Co-Authored-By` line.
- Stage each file by its path. Do not use `git add -A` or `git add .`.

## Style

Use Simplified Technical English in the Markdown documentation, the skill and the adapters. Keep the normal style of the language in code comments and in Python docstrings. Python docstrings use the Google style.
