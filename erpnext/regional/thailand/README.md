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

The Customer and Supplier lists have the button **New from Tax ID** (a user who may create the party; `customer_list.js`,
`supplier_list.js`) and a new Customer / Supplier form has **Fetch from Revenue Department**; both open ONE preview
dialog (`public/js/thailand/party_tax_id.js`, `open_tax_id_preview`): Tax ID + branch (default 00000) + Fetch, which
calls `utils.get_party_by_tax_id`. A party that already has the same Tax ID is offered with **Open Existing**: a
Customer at any branch (one Customer per Tax ID, other branches are its Addresses), a Supplier only at the same branch
(another branch may be another Supplier; RD is asked and the other branch is named as a hint). RD knowing nothing (an
individual, a non-VAT party) gives **Enter Manually** (a new form with the Tax ID and branch only). Otherwise the
preview shows the legal name (title not doubled), Tax ID, branch and RD's address in the international layout
(Siwat 17 Nov 2026; also the ZIP Code lookup of the Address form and the PND 3 / 53 and tax reports): Address Line 1
(house number, village, soi, moo, building, floor, room, road; `utils.finalize_address_dict`, a "-" part is
skipped), Address Line 2 = ตำบล/แขวง, City = อำเภอ/เขต (จังหวัด when RD has no อำเภอ, City is required), State =
จังหวัด, ZIP Code, Country Thailand, with the Company's address prefixes. Its buttons:

- **Save**: `utils.create_party_from_tax_id` (whitelisted, POST, create permission, rate limited) reads RD again on the
  server and inserts the party (type Company, Tax ID, branch, the default Customer Group and Territory of Selling
  Settings or Supplier Group of Buying Settings) with RD's address as the attributes ERPNext's own
  `create_primary_address` reads, so the Billing Address (preferred billing and shipping) is made and set primary in
  the same insert; the form then opens. A missing default returns `missing` and the dialog falls back to Edit Before
  Saving; an existing party or an unknown Tax ID creates nothing.
- **Edit Before Saving**: opens a new unsaved form (or fills the form the dialog came from) with the name, type, Tax ID
  and branch, and only the non-stored key `rd_address_lookup` = `"<tax id>:<branch>"`. On insert
  `utils.fill_rd_address_before_insert` (Customer / Supplier `before_insert`) reads RD again, only while the Tax ID is
  still the one looked up, and the Address is made the same way. The browser never supplies the stored address; RD
  down on insert is a message, never a blocked save.
- **Cancel**.

The quick entry dialog (also "Create new" from a Link field) keeps its own Tax ID, Branch Code and button, which fill
its native Primary Address fields (with Address Line 2); retyping the Tax ID or branch looks the new pair up once. The
full form has no typed address fields: an address added by hand is added after save the normal ERPNext way.

`utils.fetch_rd_vat_record` is the one RD call (also behind the Address form's "By Tax ID"): it sends only the Tax ID
and branch number, verifies the certificate (the RD chain validates with certifi; the old `verify=False` is gone),
times out after 5 s connect / 15 s read, never retries, caches an answer for an hour per Tax ID and branch, and
logs a failure to the Error Log with one friendly message. The whitelisted lookups and the preview save are rate limited (30 a minute).
Offline tests: `tests/test_rd_party_lookup.py`, `tests/js/party_tax_id.test.mjs`.
