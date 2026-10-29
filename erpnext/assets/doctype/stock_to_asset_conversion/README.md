# Stock to Asset Conversion

## Ledger preview (Preview > Accounting Ledger / Stock Ledger)

A saved conversion that is still `docstatus 0` (Draft, Pending Accounts Review, Pending Level 1 Approval) shows
two buttons under **Preview**, also to read-only reviewers. They call the standard
`erpnext.controllers.stock_controller.show_accounting_ledger_preview` / `show_stock_ledger_preview`
(`erpnext/public/js/utils/ledger_preview.js`), which post inside a transaction and roll it back.

- **Accounting Ledger**: the conversion's own `get_gl_entries` (credit Inventory, debit Fixed Asset or the Low
  Value Asset Expense account per generated asset). With `ledger_preview` set the controller balances asset
  values in memory only and never writes the conversion or its draft Assets. The Immediate Depreciation Journal
  Entry is a separate document created after approval and is not part of this preview.
- **Stock Ledger**: the outward Stock Ledger Entries `update_stock_ledger` would post for each stock item.
- **Permission**: besides read on the doctype, `before_gl_preview` requires Accounts User, Accounts Manager,
  Finance Controller, Managing Director, Chief Executive Officer or System Manager; `before_sl_preview` allows
  the same plus Stock Manager. Custodians and other readers get a permission error and no button.

Tests: `test_ledger_preview.py` (offline, mocks only).
