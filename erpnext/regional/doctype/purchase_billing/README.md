# Purchase Billing payments

A submitted, open Purchase Billing with a positive balance offers **Create → Payment Entry** to users who can
create Payment Entries. It opens one unsaved native supplier payment. Review supplier/company bank details,
amount and withholding, then save and follow the Payment Entry workflow.

The mapper reloads the billing and every invoice, checks read/create permission and uses current outstanding
balances. Paid invoices are omitted; debit notes offset the payment. Invoices must share company, supplier,
branch, invoice currency, payment accounts and accounting dimensions. Draft/cancelled invoices, held invoices
and nonpositive net balances are refused. Nothing is inserted or submitted by this action.
