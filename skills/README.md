# skills

These files teach each agent harness how to call `sdg` and where the reference documents are. The logic stays in the `sdg` package, so the files do not drift from the code.

| File | Harness | Contents |
|---|---|---|
| `claude-code/SKILL.md` | Claude Code, Codex, opencode, pi | The Agent Skill with `name` and `description` frontmatter. It triggers on a request for synthetic data. This file is the one source of the operating guide |
| `codex/AGENTS.md` | Codex | The operating guide. The root `AGENTS.md` points to it. The sync script writes it |
| `opencode/AGENTS.md` | opencode | The operating guide, loaded through `opencode.json`. The sync script writes it |
| `opencode/command/sdg.md` | opencode | The `/sdg` custom command |
| `pi/prompts/sdg.md` | pi | The `/sdg` prompt template |

The operating guide holds the `sdg` command surface, the data boundary, the workflow and a short config reference. The detail is in `docs/how_it_works.md` and in `example/`.

## The written files

Each harness reads a fixed path, so the repository commits a copy of each source at that path. The repository has no symlinks, because some container setups stop when a mounted tree holds a symlink.

| Source | Written file | Read by |
|---|---|---|
| `AGENTS.md` | `CLAUDE.md` | Claude Code |
| `skills/claude-code/SKILL.md` | `.claude/skills/sdg/SKILL.md` | Claude Code, opencode |
| `skills/claude-code/SKILL.md` | `.agents/skills/sdg/SKILL.md` | Codex, opencode, pi |
| `skills/claude-code/SKILL.md` | `skills/codex/AGENTS.md` | Codex |
| `skills/claude-code/SKILL.md` | `skills/opencode/AGENTS.md` | opencode |
| `skills/opencode/command/sdg.md` | `.opencode/command/sdg.md` | opencode |
| `skills/pi/prompts/sdg.md` | `.pi/prompts/sdg.md` | pi |

pubready keeps three guides that a person edits together. Here the sync script builds the Codex and opencode guides from the skill, so only one guide needs an edit.

Edit only a source. Then run `scripts/sync_agent_harness.sh` to write the other files. `scripts/sync_agent_harness.sh --check` fails when a written file differs from its source or when a harness path holds a symlink.
