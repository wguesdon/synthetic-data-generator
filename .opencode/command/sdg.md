---
description: Make synthetic data from a description or from glimpse reports (sdg).
agent: build
---

The user wants synthetic data. Use sdg. Follow the adapter in `skills/opencode/AGENTS.md`. Do not open real data, and do not hand-write a data generator.

Run every command from the repository root. Save the user input under `input/<project>/`, and write every output, including your own notes, under `sdg_output/<project>/`. Do not write a file outside the repository.

Request: $ARGUMENTS

These are the sdg commands:

!`uv run sdg --help`

Read the config schema with `uv run sdg schema` and a template with `uv run sdg init <format>`. Ask the user about each item that the request does not give, one question at a time. Then write the config, and run `uv run sdg check`, `uv run sdg generate`, `uv run sdg glimpse` and `uv run sdg validate`. When it finishes, give the user the config path and the output folder under `sdg_output/`.
