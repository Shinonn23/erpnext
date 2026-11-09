# Payment Entry

## Employee service compensation withholding

For compensation or services outside payroll, use **Pay**, **Party Type = Employee** and select the Employee.
**Withhold Tax → Enter type and amount** enables **Consider for Tax Withholding** and asks for an explicitly
selected **Tax Withholding Category**. Native category rates, company accounts, withholding entries and payment
tax calculations apply. For a configured 3% category and gross amount 5,000, withholding is 150 and the net bank
payment is 4,850; the Employee party amount remains 5,000. Repeated saves replace the tax rather than adding it.

This is a direct service payment, not salary calculation, an Employee Advance or an Expense Claim reimbursement.
Invoice-line category selection is unavailable for Employee payments. Employee Receive withholding is unsupported.

Submit follows the existing bank verification and amount approval workflow. The direct Employee service fee with
explicit withholding has no source reference; other Employee payments still require their approved source.
Withholding entries retain **Employee** identity instead
of substituting a Supplier. The certificate starts as a Draft when recipient tax ID or address needs completing.
Accounting enters those tax-document details explicitly; this flow does not read HR's protected national ID or
personal address. The completed certificate prints the recipient snapshots and appears in the applicable filing
report. Cancelling the payment uses the existing withholding-entry and certificate cancellation lifecycle.
