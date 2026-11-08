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
