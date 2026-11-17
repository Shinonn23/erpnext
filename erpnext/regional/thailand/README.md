# Thailand tax invoice entry

The **Purchase Tax Invoice** mini form on Purchase Invoice and Journal Entry includes **Delayed (months)**,
using the tax invoice's existing choices 0–6. Zero reports in the supplier tax invoice's month; one reports in the
following month. The offset is measured from Tax Invoice Date, which is not changed. It does not move the GL posting date.

The source Purchase Invoice, Journal Entry Account and Journal Entry Tax Invoice Detail retain `months_delayed`.
Journal tax-detail regeneration copies it, and `update_voucher_tinv` passes it to the Purchase Tax Invoice.
Its native `compute_report_date` sets Report Date, which the Purchase Tax Report filters by. Changes to a submitted
Purchase Tax Invoice also update the delay on its source where that field exists. Sales/output VAT is unchanged.
No existing tax invoices are backfilled or moved between report months.

## Customer and Supplier from a Tax ID (Revenue Department)

On a new Customer or Supplier the button **Fetch from Revenue Department** (it also runs once when the Tax ID has
13 digits) calls `utils.get_party_by_tax_id` (`public/js/thailand/party_tax_id.js`). A party that already has the
same Tax ID is offered instead of a new one: a Customer at any branch (one Customer per Tax ID, other branches are its
Addresses), a Supplier only at the same branch (another branch may be another Supplier; RD is asked and the other
branch is named as a hint). Otherwise the legal name (title not doubled), type
Company, Tax ID and branch code are filled and, on save, `custom/rd_party.create_rd_address` asks RD again and
makes the RD address the primary Billing Address (skipped when the party already has an Address). RD knowing nothing
(an individual, a non-VAT party) or being down only shows a message: the user types by hand and saving is never
blocked. The quick entry dialog (also "Create new" from a Link field) has the same Tax ID, Branch Code and button.
The form remembers the Tax ID and branch it filled (non-stored `rd_filled_key`): retyping either clears the fill and
looks the new pair up, and the server makes no RD address when the saved pair is not the filled one.

`utils.fetch_rd_vat_record` is the one RD call (also behind the Address form's "By Tax ID"): it sends only the Tax ID
and branch number, verifies the certificate (the RD chain validates with certifi; the old `verify=False` is gone),
times out after 5 s connect / 15 s read, never retries, caches an answer for an hour per Tax ID and branch, and
logs a failure to the Error Log with one friendly message. Both whitelisted lookups are rate limited (30 a minute).
Offline tests: `tests/test_rd_party_lookup.py`, `tests/js/party_tax_id.test.mjs`.
