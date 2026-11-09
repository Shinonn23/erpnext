Accounts module contains masters and transactions to manage a traditional
double entry accounting system.

Accounting heads are called "Accounts" and they can be groups in a tree like
"Chart of Accounts"

Entries are:

- Journal Entries
- Sales Invoice (Itemised)
- Purchase Invoice (Itemised)

All accounting entries are stored in the `General Ledger`

## Payment terms based on Order Date

`Payment Term`, template rows and `Payment Schedule` support **Day(s) after order date** in **Due Date Based On**.
With **Credit Days = 0**, the term is due on the order date; a name such as T/T Before Delivery is a description,
while this date basis expresses the calculation. T/T remains a Mode of Payment, not a separate document type.
Templates containing an order-based term automatically allocate payments by term and require named terms.

Sales and Purchase Orders use their actual `transaction_date`. Quotations estimate the date using their own
`transaction_date`; mapping the quotation into an order recalculates the schedule from the new order date.
Invoices require every item to reference orders sharing one actual order date. Missing links or different order
dates must be resolved rather than silently choosing the first item or the invoice date. Linked orders must be
readable by the person creating the invoice.

An invoice may therefore carry an order-based payment already overdue before its posting date. The original
order anchor is retained, including when terms are automatically fetched from the order. Invoice-based options
keep their existing behavior. An unsaved preview without an order shows no calculated date until a valid order
anchor is available; final document validation rejects an unresolved anchor. Submitted schedules are checked,
never silently rewritten. This does not make a payment automatically or block a receipt or delivery.
If a master template changes later, an existing submitted invoice or a new invoice carrying an exact submitted
order contract keeps that proven contract. Arbitrary draft term edits do not qualify: the term names, portions,
date basis, credit and discount settings must match the trusted stored schedule before it is used for validation.

## Payment Ledger
Transactions on Receivable and Payable Account types will also be stored in `Payment Ledger`. This is so that payment reconciliation process only requires update on this ledger.

### Key Fields
| Field                | Description                      |
|----------------------|----------------------------------|
| `account_type`       | Receivable/Payable               |
| `account`            | Accounting head                  |
| `party`              | Party Name                       |
| `voucher_no`         | Voucher No                       |
| `against_voucher_no` | Linked voucher(secondary effect) |
| `amount`             | can be +ve/-ve                   |

### Design
`debit` and `credit` have been replaced with `account_type` and `amount`. `against_voucher_no` is populated for all entries. So, outstanding amount can be calculated by summing up amount only using `against_voucher_no`.

Ex:
1. Consider an invoice for ₹100 and a partial payment of ₹80 against that invoice. Payment Ledger will have following entries.

| voucher_no | against_voucher_no | amount |
|------------|--------------------|--------|
| SINV-01    | SINV-01            | 100    |
| PAY-01     | SINV-01            | -80    |


2. Reconcile a Credit Note against an invoice using a Journal Entry

An invoice for ₹100 partially reconciled against a credit of ₹70 using a Journal Entry. Payment Ledger will have the following entries.

| voucher_no | against_voucher_no | amount |
|------------|--------------------|--------|
| SINV-01    | SINV-01            | 100    |
|            |                    |        |
| CR-NOTE-01 | CR-NOTE-01         | -70    |
|            |                    |        |
| JE-01      | CR-NOTE-01         | +70    |
| JE-01      | SINV-01            | -70    |

## Allocated invoice schedules and advances

For an invoice whose Payment Terms Template allocates by term, the schedule retains the full invoice contract
(after write-off), in invoice and base currencies. An allocated advance settles the source term first and then
remaining terms in schedule order, bounded by each invoice's scaled contract. An order's paid amount alone is
never inherited as invoice payment. Automatic advance fetching and manually allocated advances use the same
settlement rule; saving again does not credit them again. The last term retains the same precision residual on draft
validation and initial submission.

Payment Entry reconciliation splits the invoice reference by the amounts actually settled per term. The hidden
`advance_payment_term` records the original order term alongside `advance_voucher_type` and
`advance_voucher_no`, so unreconciliation or invoice cancellation can restore the original order attribution.
Submitted Payment Entry and Journal Entry references are the source of invoice term settlement, including
cancellation, submitted updates and reconciliation changes. Invoice locks are acquired before the payment
parent lock; rebuilding uses current locking reads, so a previous transaction snapshot cannot erase a
concurrent settlement. Old and new invoice references are refreshed when a submitted payment changes. Discounts remain separate from paid amounts; reporting converts both into its selected
currency. Both payment-reference getters offer positive remaining term balances.

Templates without term allocation retain their existing net-of-advance schedule behavior. Already posted net
schedules are not automatically converted or backfilled. The new Payment Entry Reference fields need normal
schema synchronization before this lifecycle is used; no data repair or migration is performed by these helpers.

New order payments record the discount actually applied to each reference. Reconciliation carries that exact
source discount and the original source total through partial transfers and invoice-term splits. Cancelling the
payment reverses the recorded source amounts, and unlinking restores them to the original order reference.
Older references without recorded discounts keep the existing native discount calculation; no historical
allocation or discount is invented or backfilled. Base settlement follows each stored base contract amount,
so an entirely settled term closes in both currencies even when multiplying by the exchange rate independently
would leave a rounding cent. Base outstanding is proportional to the stored contract; the base discount is
the residual after base paid and base outstanding, so reports preserve the same identity after rounding.

Cancelling an authorized source invoice or payment also cancels its verified, system-generated Exchange Gain
Or Loss journals. Authority is passed only by the native cancellation lifecycle through an internal identity
token. The helper rechecks the stored source's cancellation permission and cancelled status, and locks and
verifies each generated journal's type, submitted status, company and exact source reference before running
its full native cancellation. Manually cancelling a Journal Entry and reconciliation callers retain their
normal permissions; no Journal Entry cancel role or workflow bypass is granted.

Payment Entry source attribution and recorded term discounts are server-owned, including the native
`advance_voucher_type` and `advance_voucher_no` fields. Draft saves and initial submission clear client-supplied
values. Submitted updates and cancellations restore attribution from the locked stored child rows and reject
moving, deleting or duplicating an attributed row or changing its reference, term, allocation or total binding.
Only the native reconciliation helper can mint a one-use internal capability to change or split that attribution;
it survives a deferred-save batch until its single save. Stored legacy attribution is preserved without inference.
