# Journal Entry

Journal entry for accounting entries including payments.

A direct supplier expense payment can use **Create Tax Invoice** and **Consider for Tax Withholding** together.
Select the supplier, tax invoice number and date on the configured input VAT row. For one supplier and one
Bank/Cash credit row, withholding uses that supplier without putting a Party on the bank GL entry. The existing
Tax Withholding Category determines the rate, threshold and liability account; the automatic tax row reduces the
bank payment. For expense 10,000 plus input VAT 700 at 3%, the gross payment 10,700 becomes bank 10,400 and WHT 300.
The expense and input VAT are debits; Bank/Cash and calculated WHT are credits. Reversed input VAT is reported as
a Debit/Credit error before resolving the supplier, so an entered supplier is not reported as missing.

Repeated saves replace the calculated tax. Turning automatic withholding off restores the gross payment and removes
its generated tax rows. When a Draft has manual rows for the selected category's WHT account and automatic withholding
is enabled, Save asks **Withholding Calculation**: **Automatic** adopts those rows, balances the bank payment and
recalculates through the native controller; **Manual** keeps the entered ledger amounts and disables automatic
withholding. Manual mode creates no native Tax Withholding Entries or automatic certificate; these must be prepared
separately. The dialog changes nothing until **Apply and Save**. Empty WHT template rows are removed in Automatic mode
before native zero-row validation. Mixed managed/manual tax, multiple suppliers or ambiguous settlement rows are refused.
Supplier payment holds and
frozen/disabled checks still apply. Approval, tax invoice creation, withholding certificates and cancellation use the
existing document lifecycle; saving a Draft does not post accounting entries or transfer money.

Saved Drafts expose **Preview → Accounting Ledger**, using the native ledger-preview permission checks and rollback.
Save changes before previewing: the preview reads the stored document. Submitted entries use **View → Ledger**.

Thai tax dialogs load through `erpnext.bundle.js`, whose generated filename changes with its contents. This keeps
Journal Entry validation and its shared tax helpers in sync when browsers cache assets between deployments.
