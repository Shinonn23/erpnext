# Sales Billing payments

A submitted, open Sales Billing with a positive balance offers **Create → Payment Entry** to users who can
create Payment Entries. It opens one unsaved native receipt for the billing's invoices. Review the bank details,
amount and withholding before saving and following the Payment Entry workflow.

The mapper reloads the billing and every invoice, checks read/create permission and uses current outstanding
balances. Paid invoices are omitted; credit notes offset the receipt. Invoices must share company, customer,
branch, invoice currency, payment accounts and accounting dimensions. An invalid or nonpositive net balance is
refused. Nothing is inserted or submitted by this action.

**Create Multi-Payments** remains available separately for splitting receipts across payment modes. Its existing
behavior saves Draft Payment Entries; users still approve/submit them through their payment workflow.
