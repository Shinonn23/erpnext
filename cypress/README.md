# Custody Loan browser E2E

This suite uses Frappe's existing Cypress 13 runner. Python prepares **prerequisites only** and verifies committed effects; the browser creates Material Requests and performs every loan, pick, delivery, inspection, shipment, return, adjustment, sale and cancellation action using visible controls. There is no Playwright stack, production endpoint, database reset or global fixture truncation.

## Current verification status (2026-10-12)

- Written: 15 browser cases, test-only Python helpers, read-only site preflight, condition inventory and evidence capture.
- Passed: 16 helper **unit** tests without a site/database; Python lint/format and JavaScript syntax checks.
- Browser cases: **unrun / blocked**, not passed. No browser workflow fixtures have been created on `local.erpnext`.
- Site inventory: Frappe and ERPNext only, `allow_tests=true`, fiscal year covering 2026. Effective metadata differs from the checkout in 18 fields/layout controls; 64 Custom Fields and 60 Property Setters require a source/value provenance audit. These numbers are inventory findings, not a claim that all those records are external customizations. Examples include native accounting dimensions and regional setup records.
- Inventory evidence: ignored local file `cypress/artifacts/evidence/preflight-local.json`. It includes metadata conditions, hook targets, source paths and every blocker, without configuration credentials or transaction data.
- UAT: not specified; its Client Scripts, Server Scripts, Custom Fields, Property Setters, permissions and hooks have **not** been surveyed. UAT is read-only for this assignment.

Do not report every condition as proven while browser cases are unrun. The table below maps checks to code; actual true/false results must come from a completed run.

## Preflight and running

From the bench directory, perform the read-only inventory first:

```sh
bench --site local.erpnext execute erpnext.loan.doctype.custody_loan.custody_loan_ui_preflight.inventory
```

Resolve every reported blocker through source attribution and authorized site preparation. The inventory deliberately refuses to treat settings-produced fields/setters as harmless merely because two apps are installed. Its field-source allowlist currently covers Thailand's exported constants; other native regional, setup and dimension records need an exact source/value audit and corresponding preflight attribution before they can be accepted. It also checks effective field properties, schema columns, active scripts/workflows, custom permissions and native prerequisites. Virtual fields correctly do not require database columns. No migration, app installation, configuration change or customization removal is automatic.

Confirm the inventory URL is reachable, the server is a development server with tests enabled, and an Administrator password is available through a local secret mechanism. Set `CYPRESS_adminPassword` and `CYPRESS_custodyLimitedPassword` in the environment without committing them; the latter must be at least 12 characters. `bench` can also inject its configured admin password. Do not paste passwords into reports.

Once preflight passes:

```sh
bench --site local.erpnext run-ui-tests erpnext --headless --spec erpnext/loan/doctype/custody_loan/ui_test_custody_loan.js
```

Repeat that exact command as a separate invocation to check isolation. Do not run site-mutating tests concurrently. Frappe's runner installs its existing Cypress/plugin dependencies when absent; it is not a read-only availability check. No Cypress dependency was installed for this blocked run. Use `--browser /absolute/path/to/chrome` if Chrome is not on the runner's path.

Pure helper unit checks, from the ERPNext app directory:

```sh
/home/shinon/frappe/connect_diagnostics/env/bin/python -m unittest erpnext.loan.doctype.custody_loan.test_custody_loan_ui_test_helpers -v
```

After a browser run passes, run the existing `test_custody_loan_integration` module and the affected Material Request integration cases on the same preflight-approved site using native `bench run-tests`. Their rollback-based fixtures are separate from browser fixtures. These native suites were not executed here.

See [the condition matrix](CONDITIONS.md) for JS branches, query filters, exact metadata predicates and their case mapping.

## Scenario matrix

Every full loop owns a UUID-tagged company, item, warehouses/storage locations, party/address/contact and opening fixtures. It uses quantity 2 at valuation 100 for stock; asset fixtures have quantity 1 and value 100. Transfers use the same stock account on both warehouses, so physical custody changes without a net GL change. Python assertions check exact accounts/amounts for write-off and asset sale, not only whether debits and credits balance.

| Case | Kind | Visible lifecycle / entry point | Independent expected effects | Reversal / rejection |
| --- | --- | --- | --- | --- |
| UI-MR-01 | Browser UI | Change Purpose, Borrow Item Type and Recipient | Stock/Asset and Customer/Employee/Supplier/Contact visibility/mandatory controls | Purchase/Demo hide or change loan controls |
| UI-MR-CHILD-01 | Browser UI | Open child rows under six request combinations | Asset/location/UOM requirements and read-only controls | Stock/asset true and false states; draft quantity controls |
| ASYNC-01 | Browser UI lifecycle | Submit real Pick List; delay real get-value response; navigate via UI | Delivery Note action appears only after submitted pick | No action leaks to another form; no forged response |
| STOCK-01 | Full loop | MR → Loan → Pick → DN → Return 1 → Accepted QI → Receive → Return 1 | Source/loan stock 2/0 → 0/2 → 1/1 → 2/0; returned/outstanding 0/2 → 1/1 → 2/0 | Return/Adjustment disappear at Returned |
| STOCK-02 | Full loop | Full return, rejected then accepted inspection | No receipt stock before accepted QI and receiving DN submit | Failed submits preserve docs, ledgers and quantities |
| SHIP-01 | Full loop | Outgoing/return Shipment, Confirm Receipt, signature, QI | Pending Shipment blocks Return; return waits for Delivered and Accepted QI | Premature inspection/receipt rejected without side effects |
| SERIAL-01, BATCH-01 | Full loop | Wrong identity/over-return → partial return → write-off | Original identity, exact stock/value and bundle quantities; outstanding 0 | Adjustment cancellation restores remaining stock and GL |
| ASSET-01, ASSET_EMPLOYEE-01 | Full loop | Loan → Movement → Return → cancel Return → cancel Loan | Location, employee custody, status, movements and reports | Return dependency prevents early Loan cancel |
| ASSET-SALE-01 | Full loop | Customer Asset Sale Invoice → cancel through Loan action | Sold/Closed, receivable + asset disposal + gain accounts; no stock effect | Cancellation restores active loan and reverses GL |
| ASSET-SALE-DRAFT-01 | Browser lifecycle | Open/delete linked draft sale through Loan | Active custody, no accounting effect | Draft-only Delete action; submitted-only Cancel action |
| CANCEL-01 | Full loop | Full stock return → rejected Loan cancellation → cancel Return → cancel Loan | Native inactive/reversal records; original source stock restored | Submitted Return dependency enforced |
| PERM-01-stock, PERM-01-asset | Browser authorization lifecycle | Restricted reader opens one shared Loan; direct public API attempts | Read only; create/open actions absent; no transactional effect | API must return PermissionError; no global DocPerm replacement |
| Helper unit tests | Unit | Validation/manifest/cleanup/GL calculation helpers | Traversal/foreign ownership rejected, exact accounting scope | Failed cleanup rolls back and still revokes temporary access |

The return path always includes QI, even with `requires_shipment=0`. A full physical return ends in `Returned`; `Closed` means the remaining quantity was adjusted or sold. Submission of an outgoing Shipment posts its Delivery Note automatically; the test does not submit that note a second time.

## Evidence and cleanup

Each invocation gets a timestamp directory under ignored `cypress/artifacts/`, containing videos for **both** pass and failure, before/after-reload screenshots and JSON checkpoints. Every successful Submit/Cancel and Shipment delivery confirmation records Python snapshots before/after reload and verifies any existing loan; rejected transitions keep independent before/after snapshots. JSON includes expected results, stable documents, document numbers, Stock/GL ledgers, serial/batch bundles, asset state, reports, sanitized method/URL/status network logs and uncaught JavaScript errors. Automatic retries are disabled. No framework support file that suppresses JS exceptions is imported.

Test endpoints use `whitelist_for_tests` plus manager authorization and a server-held manifest in the site's private `custody-e2e/` directory. A supplied ID cannot select arbitrary data or cancel unrelated documents. The manifest has mode 0600 and remains after success/failure. A restricted user receives read access to **one** owned Loan via DocShare; user, share and its private desk role are revoked in cleanup, including failure/retention paths. Employee fixtures do not create login accounts.

Browser submissions commit as separate HTTP requests, so Python integration-test rollback cannot clean them up. Cleanup uses normal source cancellation in dependency order and a savepoint around each attempted operation; errors retain the manifest and fail the cleanup check. No forced ledger deletion or validation bypass is used. UUID fixture masters, their native chart/defaults and company-specific Thai Tax Settings row remain for audit; canceled stock opening entries leave the fixture warehouses at zero.

Native posted Shipment and Delivery Note cancellation currently block one another. For a fully returned Shipment loan, cleanup retains the isolated terminal flow and its opening fixtures, and records the retention reason. An incomplete posted Shipment flow is a cleanup error and requires completing its return through UI. This is a documented native lifecycle limit, not a skipped assertion or an implicit database reset. Fixture quantities never reuse UAT's starting stock.

The Adjustment schema currently bases serial/batch visibility on `frappe.get_meta('Item', doc.item_code).has_*`. Metadata is not an Item record; the identity UI assertions intentionally may expose this existing defect. No production fix is included in this testing task.

The Register SQL currently sums requested `item.qty` under the `issued_qty` label, while the browser verifier expects physically issued quantities (`item.issued_qty`). The Pending Pick checkpoint intentionally detects this discrepancy. This is a static finding; no browser failure or production fix is claimed.

MR progress currently preserves `Closed` in `update_custody_loan_progress` even after a sale/adjustment is reversed. The verifier requires MR quantities/status to reopen with the restored outstanding amount, so those reversal cases can expose this static finding too. Production behavior was not changed.
