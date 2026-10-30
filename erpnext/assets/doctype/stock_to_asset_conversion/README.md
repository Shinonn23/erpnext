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

## Submit and repost (Repost Item Valuation)

- **Submit**: `on_submit` sets the process-local `flags.submitting` around `make_gl_entries`. Only then does
  `get_gl_entries` give each stock row's bundle/rounding difference to its last Asset and save that value on
  the row and the draft Asset, so the journal balances (credit Inventory = Stock Ledger value, debit Fixed
  Asset or Low Value Asset Expense = Asset values).
- **Repost**: a backdated stock transaction makes Repost Item Valuation rebuild the journal of every later
  stock voucher (`erpnext.accounts.utils.repost_gle_for_stock_vouchers`, which calls
  `get_gl_entries(inventory_account_map)` and `make_gl_entries(gl_entries=..., from_repost=True)`; both
  signatures match `StockController`). For a submitted conversion this is read only: no Asset, row or flag is
  written. The Inventory credit follows the current Stock Ledger value; each Asset debit keeps the value saved
  at submit, so the Fixed Asset account stays equal to the Asset register. A difference between the two is
  booked per stock row on the company's **Stock Adjustment Account** (remark "stock value restated by a
  repost; the Asset value is unchanged"). Changing an Asset's value is accounting's decision through an Asset
  Value Adjustment. With unchanged values the rebuilt journal equals the submitted one, so nothing is reposted.
- **No Stock Adjustment Account**: the conversion's ledger is left as posted and an Error Log names the
  conversion; the other vouchers of the repost go on (raising would roll back and fail the whole repost).
  Set the account and repost again.
- Repost Accounting Ledger does not offer this doctype (not in the `repost_allowed_doctypes` hook that fills
  Accounts Settings > Repost Allowed Types), so only Repost Item Valuation reaches it.

Tests: `test_repost_gl_entries.py` (offline, mocks only).
