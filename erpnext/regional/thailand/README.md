# Thailand tax invoice entry

The **Purchase Tax Invoice** mini form on Purchase Invoice and Journal Entry includes **Delayed (months)**,
using the tax invoice's existing choices 0–6. Zero reports in the supplier tax invoice's month; one reports in the
following month. The offset is measured from Tax Invoice Date, which is not changed. It does not move the GL posting date.

The source Purchase Invoice, Journal Entry Account and Journal Entry Tax Invoice Detail retain `months_delayed`.
Journal tax-detail regeneration copies it, and `update_voucher_tinv` passes it to the Purchase Tax Invoice.
Its native `compute_report_date` sets Report Date, which the Purchase Tax Report filters by. Changes to a submitted
Purchase Tax Invoice also update the delay on its source where that field exists. Sales/output VAT is unchanged.
No existing tax invoices are backfilled or moved between report months.
