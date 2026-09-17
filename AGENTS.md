# AGENTS.md

## Think Before Coding

Do not make hidden assumptions.

- Understand the requested outcome before implementing.
- Surface material ambiguity or tradeoffs when they affect the solution.
- Check the surrounding code before changing it.
- If there is a substantially simpler solution, prefer it.

## Simplicity First

Write the minimum code necessary to solve the current problem.

- No speculative features.
- No abstractions for single-use logic.
- No configurability or extension points that were not requested.
- No defensive machinery for impossible or purely hypothetical states.
- Prefer straightforward code over clever architecture.
- Prefer a small amount of obvious duplication over a premature abstraction.
- Abstract only when duplication represents the same stable concept, not merely similar-looking code.
- Keep related logic together. Do not split simple behavior across additional files, classes, modules, or layers without a concrete reason.
- Keep control flow shallow and easy to trace.
- Prefer simple control flow and early returns over unnecessary nesting.
- Avoid accumulating special cases, fallback paths, defensive branches, and compatibility paths without a concrete requirement.
- If the implementation is becoming much larger than the problem, simplify it.
- Prefer to never use subagents as they're expensive but if they provide a very substantial benefit, ask me before using them.

## Complexity Budget

Complexity metrics are warning signals, not goals.

- For new or substantially changed functions, treat cyclomatic complexity above 10 as a prompt to reconsider the implementation.
- Treat cognitive complexity above 15 similarly when the repository tooling supports that metric.
- These are review thresholds, not automatic refactoring requirements.
- A coherent function may exceed a threshold when splitting it would make the code harder to understand.
- Do not split logic into arbitrary helper functions merely to lower a complexity score.
- Do not introduce interfaces, classes, modules, or abstractions merely to move complexity elsewhere.
- If the repository already defines complexity limits, follow the repository's existing limits instead.
- Do not add a dependency, linter, analysis service, or repository-wide scan solely to enforce these thresholds.

## Surgical Changes

Touch only what the task requires.

- Do not refactor unrelated code.
- Do not reformat unrelated files.
- Do not rename or reorganize things opportunistically.
- Do not upgrade dependencies as part of an unrelated change.
- Preserve existing patterns and architecture unless changing them is necessary for the task.
- Every changed line should be traceable to the requested outcome.

## Goal-Driven Execution

Determine what observable result would prove the task is complete, then verify that result.

Verification must be proportional to the change and optimized for useful signal per unit of time.

- Start with the narrowest relevant check.
- Run the specific affected test before a package or repository-wide suite.
- If a specific package or module changed, scope linting, typechecking, builds, and tests to it when the tooling supports that.
- Prefer an existing relevant test over creating a new test harness.
- Prefer one high-signal regression test to many low-value tests.
- Do not add tests merely because code changed.
- Do not create generic "smoke tests" when a direct behavioral check exists.
- Do not run the full test suite, full workspace build, broad integration suite, coverage suite, benchmark suite, or end-to-end suite by default.
- Do not launch a validation command reasonably expected to take several minutes unless it directly exercises the changed behavior, the user requests broad validation, or repository instructions explicitly require it.
- Let CI perform broad validation when targeted local verification is sufficient.

Never claim a test or command passed unless you actually ran it.

## Testing Value

Tests should protect meaningful observable behavior.

Avoid tests that merely verify:

- constants or static data;
- trivial accessors;
- framework or library behavior;
- static wiring with no meaningful behavior;
- implementation details;
- types already enforced by the type system;
- behavior adequately exercised by a more direct existing test;
- coverage percentage rather than useful behavior.

When fixing a bug, add a focused regression test when practical and when it meaningfully protects against recurrence.

Do not weaken, delete, skip, or relax tests or checks merely to make them pass.

## Architecture

Match the architecture already present.

- Do not redesign working systems while implementing an unrelated feature or fix.
- Reuse existing code and patterns before introducing new infrastructure.
- Before creating a helper, module, service, interface, adapter, factory, registry, wrapper, or abstraction, check whether an appropriate one already exists.
- New indirection should make the current problem simpler, not merely make hypothetical future changes easier.
- Do not optimize for imagined future scale, extensibility, or reuse without a current requirement.

## Dependencies

- Do not add a dependency when the standard library or an existing dependency solves the problem reasonably.
- Do not replace libraries or frameworks as part of an unrelated task.
- Do not add tooling solely to satisfy process when the same confidence can be obtained with existing tools.

## Comments and Documentation

Code should usually explain itself.

- Comments should explain non-obvious constraints, invariants, or reasons, not narrate obvious code.
- Do not add large explanatory comments, architecture documents, READMEs, migration guides, or examples unless the change requires them or the user asks for them.
- Do not document speculative behavior or hypothetical extension points.

## Before Finishing

Inspect the diff.

- Remove complexity introduced by your own change that is unnecessary.
- Remove unused helpers, abstractions, fallback paths, configuration, and comments introduced during implementation.
- Do not clean up unrelated pre-existing code.
- Confirm that the resulting patch is easy to understand from the diff alone.

Report:

- what changed;
- what was actually verified;
- any broader checks intentionally not run.

## Project Context

Keep project context brief.

Reference existing documentation where relevant rather than duplicating it here.

For example, a pointer such as:

- For changes to service boundaries, consult `docs/architecture.md`.

(This is just an example, there might not be a doc like that in this repo.)

Provide only the context needed to direct the agent to the relevant source of truth. Do not require the agent to read every project document before every task.
