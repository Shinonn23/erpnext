# ERPNext agent coordination

Explicitly use subagents for suitable independent work. Keep small or dependent work with the Coordinator. Shared role configs live in `.codex/agents/`; `.codex/config.toml` sets Coordinator defaults and the three-child limit. Project configs define this repository's role defaults; keep credentials and machine permissions in user config.

## Roles and orchestration
- Coordinator: gpt-6.1-sol, medium reasoning; plans, assigns ownership, integrates results, and validates completion.
- Explorer: gpt-6-luna, high reasoning; read-only investigation.
- Implementer: gpt-6.1-sol, medium reasoning; edits only assigned files.
- Reviewer: gpt-6.1-sol, high reasoning; read-only correctness review.

Give each child a goal, context, assigned files, acceptance criteria, allowed actions, and expected evidence. Only the Coordinator spawns agents. Use Explorer, then Implementer, then Reviewer when useful; parallelize independent work only. Wait for required results, resolve findings, and stop unnecessary child work before reporting completion. Read-only roles must not mutate files or application data even if runtime permission overrides relax sandbox defaults.

Keep role models fixed. If Explorer evidence is insufficient, explain why and spawn a generic/default read-only Explorer with gpt-6.1-sol and high reasoning for that task. Do not use the custom explorer for escalation because its configured model can override spawn values. Do not change persistent configuration or other role models automatically. Do not create sidebar chats as a substitute for subagents.

Before implementation, trace relevant DocTypes, controllers, hooks, document lifecycle (validate, submit, cancel), permissions, and persisted effects. Check accounting and stock invariants when affected. Assign disjoint file ownership, preserve existing uncommitted work, and never revert another agent's or the user's changes.

Explorer and Reviewer are read-only. Implementer edits only assigned scope. The Coordinator integrates results, resolves review findings, and runs necessary checks. Keep at most three child agents active; respect a lower runtime limit.

Serialize tests that write to the same Frappe site/database. Use a user-designated clean test site for business workflow tests and follow the applicable ERPNext testing skill. Do not infer a development or production site is safe for testing. Migrations and application data changes are controlled by the Coordinator and require applicable user authorization. No migration or application database write is needed to validate agent configuration.

If named custom agents are unavailable, use generic spawn tools with explicit role/model/reasoning settings and role constraints as described in user-level instructions. If delegation or a model is unavailable, the Coordinator continues locally and reports the limitation. Respect Plan Mode and higher-priority instructions.
