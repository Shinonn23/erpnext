# User-specified clean test site

## Before any site execution

Require the user to identify the test site for the current assignment. Treat the
name as a literal argument and quote it safely; never interpolate untrusted shell
syntax. Locate the bench through workspace paths, without reading or printing
database passwords, encryption keys, or other site configuration secrets.

Code reading and writing tests can proceed without a site. Test execution cannot.

## Read-only checks

Run these checks on the supplied site before running tests:

1. List installed apps, for example `bench --site SITE list-apps`. The exact set
   must be `frappe` and `erpnext`; Frappe is the required framework. Any third app
   blocks execution, even if it appears unrelated.
2. Inspect effective hooks and active database customizations for the assigned
   DocTypes and their dependent parent/child documents. Relevant sources include
   Custom Field, Property Setter, enabled Server Script and Client Script, and
   custom DocTypes referenced by the flow. Use metadata/DB read APIs and inspect
   names and target DocTypes before retrieving script content.
3. Attribute customization records to the checkout: inspect native JSON, fixture
   exports, patches, setup code, and app hooks. A `custom=1` field is not by itself
   evidence of an external customization; ERPNext can create such fields itself.
   Enabled global event/API scripts affecting the flow also need examination.
4. Compare effective metadata and hooks with the ERPNext source under test.
   If an affecting record has no justified source in the checkout, or provenance
   cannot be established, stop execution and report it. Do not assume that two
   installed apps means the site has never had third-party customizations.
5. Confirm that prerequisite DocTypes/columns and test configuration are ready
   for the checkout. Report missing schema, fixture bootstrap failures, disabled
   tests, or app-version mismatch requiring preparation. Do not repair environment
   prerequisites by migration/configuration changes without a separate instruction.

These checks establish fitness for the assigned flow, not a guarantee that every
record in the site is untouched. Do not call a site clean solely because a test
command succeeded. Never erase records to force a clean result.

## Native execution

From the bench directory, substitute the user-supplied site and real test module:

```text
bench --site SITE run-tests --app erpnext --module MODULE --test TEST_METHOD
bench --site SITE run-tests --app erpnext --module MODULE
```

Check `bench run-tests --help` for the installed version before choosing selectors.
Do not pass a made-up module or blindly run every ERPNext test. Read the runner and
`before_tests` hooks to understand native fixture bootstrap and schema preparation;
if preparation would violate the allowed environment boundary, report it first.
Native test fixtures are allowed data changes on the designated test site. Do not
run workflow tests or fixture bootstrap on a different site.

Run new cases, relevant regressions, and a repeat in separate native test invocations.
Capture enough output to report exact executed counts and failures; large trace
locals can contain business data, so summarize failures rather than dumping them.

## Completion record

Include the site name, installed app names, affecting-customization check result,
commands, selected modules/cases, outcomes, and any blockers. The skill does not
authorize site creation/reset, app installation/removal, migration, automatic
configuration changes, or production execution.
