---
name: erpnext-full-loop-tests
description: Write, extend, or review full loop integration tests for any ERPNext module or DocType using its native document lifecycle, test bases, and fixtures. Use when asked to test business workflows and their persisted effects, including cancellation or reversal where supported. Requires a user-specified clean Frappe/ERPNext test site before execution.
---

# ERPNext Full Loop Tests

Use this workflow for the module or feature the user assigns. Stock, accounting,
manufacturing, assets, projects, and other modules have different lifecycles;
choose assertions from the actual controller and business requirements.

## Inputs and execution boundary

- Establish the feature, expected behavior, and test scope from the user's request.
  Do not expand one DocType assignment into testing the entire ERPNext installation.
- Obtain a test site explicitly specified by the user for this assignment. If absent,
  ask for it before executing any site command. Continue reading code and designing
  tests while waiting. Never infer it from the current site, defaults, or an earlier
  unrelated assignment. Do not embed a site name in Python tests.
- Before tests, follow [site preflight](references/site-preflight.md). The site
  must contain only Frappe and ERPNext and no external customization affecting the
  tested documents. Changes belonging to the ERPNext checkout under test are allowed.
- If the site fails preflight, stop site execution and report the evidence. Do not
  create/reset sites, install/uninstall apps, migrate, remove customizations, or
  modify site configuration automatically. Do not switch sites to make tests pass.

## Ground the tests in native behavior

Read the owning DocType JSON and controller, applicable hooks, linked document
controllers, and nearby native tests before writing. Identify which public entry
points users actually exercise and where each persisted effect is produced.
Read [native patterns and template](references/native-patterns.md) when choosing
the test base and helpers. Verify examples against the current checkout; native
tests are useful patterns, not authoritative specifications for every feature.

Before edits, present a compact scenario matrix:

| Scenario / test name | Kind | Real fixtures | Lifecycle / entry point | Independent expected effects | Reversal / rejection |
| --- | --- | --- | --- | --- | --- |

Distinguish these kinds in the matrix and final report:

- **Unit:** isolated calculation or validation logic.
- **Smoke:** import or execution succeeds, possibly with empty results.
- **Full loop:** real documents traverse a meaningful business lifecycle, with
  persisted state and side effects verified at its important transitions.

Cover the assigned happy path, important variations, invalid transitions, and
downstream dependencies. Cancellation, return, amendment, closure, or completion
belongs in the matrix only where the feature supports it. Keep focused scenarios
separate rather than building one large test whose failure hides later assertions.

## Write native integration tests

- Place tests beside the responsible DocType or report using its existing naming
  convention and test base. Reuse native fixture builders; inspect whether they
  already insert or submit. Normally use `ERPNextTestSuite` for ERPNext integration
  tests; preserve a different native base where the owning module uses it.
- Exercise real public document APIs (`insert`, `save`, `submit`, `cancel`, native
  return/mapping functions) and reload affected documents before asserting. Do not
  invoke an internal lifecycle hook alone and call that a full loop.
- Do not mock document submission, ledgers, payment allocation, asset processing,
  or report results in a full loop. If an unrelated external service needs a stub,
  keep it at the external boundary and disclose it.
- Compute expected results from fixtures and business rules, not solely from a
  second report or the implementation being tested. Read
  [domain assertions](references/domain-assertions.md) only for relevant domains.
- Assert state, ownership/linkage, exact quantities and amounts using native
  precision rules, and absence of duplicates where applicable. Balanced GL alone
  does not prove the right accounts or amounts were used.
- For cancellation, verify the native net effect and inactive/reversed records;
  do not require ledger rows to be deleted. For rejected operations, assert no
  partial business effects using the transaction boundary described in the template.
- Create independent fixtures. Set explicit posting dates/times when ordering or
  backdating matters; use a valid fiscal period. Avoid sleeps, random selection of
  existing transactional records, and assumptions about rows from another test.
- Reuse rollback and cleanup in the native base; call `super()` when overriding
  setup/teardown. Restore users/settings/caches and other nontransactional state
  with cleanup or `try/finally`. Do not commit test transactions or bypass business
  validations to obtain a passing result.
- Share only fixture preparation or meaningful assertions used more than once.
  Do not introduce a custom test framework, general runner, or production API
  merely to support these tests.

## Run, diagnose, and report

On the preflight-approved site, use native `bench run-tests`: run new tests first,
then the affected existing native tests, and repeat the relevant suite to expose
state leakage. Choose the smallest suite that proves the requested coverage.
Commands and handling of prerequisites are in the site reference.

When a test fails, determine whether it is a test mistake, missing prerequisite,
existing defect, or regression. Preserve assertions that express the requirements.
Fix implementation only when the current task authorizes it; otherwise retain the
regression test and report the defect. Broad retries, skips, relaxed expectations,
or additional mocks are not substitutes for diagnosis.

Report the supplied site, installed apps and preflight outcome, exact commands,
tests actually run and pass/fail/skip results, full loop versus smoke coverage,
implementation fixes if authorized, and unresolved prerequisites or risks. Never
claim an entire suite passed when only selected tests ran. If blocked before
execution, label tests unrun and state what needs preparation.
