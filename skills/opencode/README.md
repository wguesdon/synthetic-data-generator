# opencode adapter

The repository commits three connections between sdg and opencode.

## Always-on instructions

opencode reads the root `AGENTS.md` as project instructions. It does not read `CLAUDE.md` when `AGENTS.md` exists. The `instructions` array in the root `opencode.json` also loads this adapter:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "instructions": ["skills/opencode/AGENTS.md"]
}
```

Do not replace the root `AGENTS.md` with this adapter. The root file also holds the rules for work on the repository. Do not edit `AGENTS.md` in this folder, because `scripts/sync_agent_harness.sh` writes it from the skill.

## The skill

opencode finds the skill in `.agents/skills/sdg/SKILL.md` and in `.claude/skills/sdg/SKILL.md`. The two copies are identical. opencode logs a "duplicate skill name" warning and keeps one of them.

## The /sdg command

`command/sdg.md` is an opencode custom command. The repository commits a copy of it in `.opencode/command/sdg.md`. The line ``!`uv run sdg --help` `` runs before the prompt goes to the model, so the model sees the command list.

Invoke the command with a request:

```
/sdg 12 patients at 2 visits, one lab table as CSV with 6 columns
```

Edit only `command/sdg.md`, then run `scripts/sync_agent_harness.sh` to write the copy.
