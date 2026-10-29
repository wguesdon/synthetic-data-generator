# pi adapter

pi reads the root `AGENTS.md` as project instructions. It loads the first file that it finds in a directory, in the order `AGENTS.override.md`, `AGENTS.md`, `CLAUDE.md`. It therefore does not load `CLAUDE.md` in this repository.

| Item | Path | Invocation |
|---|---|---|
| Skill | `.agents/skills/sdg/SKILL.md`, a copy of `skills/claude-code/SKILL.md` | `/skill:sdg` |
| Prompt template | `.pi/prompts/sdg.md`, a copy of `skills/pi/prompts/sdg.md` | `/sdg <request>` |

pi loads a project skill and a project prompt template only after you trust the project. A pi prompt template does not run shell commands, so the template tells the model to run `uv run sdg --help` first.

Edit only the sources. Then run `scripts/sync_agent_harness.sh` to write the copies.
