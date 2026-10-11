# Assertions selected by the assigned domain

Read only the sections that affect the user's workflow. These are examples of
observable invariants, not a mandatory matrix for every DocType.

## Stock and accounting

- Check item/warehouse/dimension movement, quantity and valuation at each important
  transition against independent fixture arithmetic. Warehouse running quantities
  may differ from dimension-specific quantities; use the appropriate invariant.
- Verify ledger ownership by voucher type/name and detail row, cancellation flags,
  and serial/batch linkage where applicable. Cancellation may retain immutable
  entries and post reversing effects instead of deleting rows.
- Assert the expected debit/credit accounts, amounts, company and relevant finance
  book/currency. Also verify balanced totals, no unintended duplicate postings,
  and net restoration after cancellation with native currency precision.
- Transfers between warehouses sharing one inventory account may legitimately
  produce no GL; derive expectations from actual company/account configuration.

## Payments, buying, and selling

- Verify invoice/order references, allocated amounts, party/account/currency,
  outstanding balance, paid/unpaid status and payment ledger where used.
- Partial payment/return should change only the expected amount/quantity. Check
  cancellation restores allocation and status; downstream documents may block
  cancellation until their own effects are reversed.
- Use native return/mapping functions rather than constructing a return by setting
  flags without preserving references and quantities.

## Manufacturing, planning, and reservations

- Check required quantities, reserved/projected balances, transferred/consumed
  materials, produced quantities, operations and completion status as relevant.
- Compare actual baseline and expected deltas. A change of status alone does not
  prove materials or reservations were handled correctly.
- Verify partial completion and supported reversal/closure paths without borrowing
  fixtures or results from another test method.

## Assets

- Check source links, draft/submission ownership, original cost, carrying value,
  depreciation schedule/finance book, GL and linked Journal Entries as applicable.
- Verify immediate versus scheduled effects using explicit posting dates. Retained
  historical cost can differ from current carrying value; assert both when needed.
- Cancellation must undo the relevant net accounting effects and respect active
  movements, sales, depreciation, or other dependent transactions.

## Other modules

For projects, quality, maintenance, CRM, or another module, derive assertions from
its own controller and native tests: lifecycle status, relationships, calculations,
permissions and persisted consequences. A DocType need not produce Stock or GL to
have a meaningful full loop. Include authorization checks when they are in scope;
do not use Administrator-only success as proof of role restrictions.

## Reports

- Build nonempty relevant fixtures and assert selected rows, identities, quantities,
  values and totals calculated from those fixtures. Use native filters and account
  for report rows returned as dictionaries versus lists.
- For grouping switches, assert both row partitioning and invariant totals;
  transaction/detail reports should preserve their identities rather than blindly
  aggregate everything. Planning figures and accounting breakdowns must not repeat
  totals across groups.
- For diagnostics, test a healthy case and a controlled anomaly appropriate to the
  detector. If intentionally editing persisted data is necessary, isolate that
  fixture transaction and clearly distinguish it from a normal document lifecycle.
- Execution without an exception, especially with empty rows, is smoke proof only.
  Use native `execute_script_report` where suitable for that limited purpose.
