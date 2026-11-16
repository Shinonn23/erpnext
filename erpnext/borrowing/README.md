# Borrowing document flow

The module Borrowing (formerly Loan; the DocTypes Custody Loan ... were renamed Borrowing ... on 16 Nov 2026, patch `patches/v16_0/rename_custody_loan_to_borrowing.py`) holds what a Material Request lends out. Three request types lend:

- **Borrow**: an Employee borrows and holds the goods. The borrower is always an Employee.
- **Customer Borrow**: a Customer borrows to use and returns (for example a laboratory reagent). The borrower is always a Customer.
- **Customer Demo**: a demonstration at a Customer, unchanged.

The request's Borrower Type is derived from its type (read only), and a Borrowing refuses a borrower of another type than its request names (`BORROWER_TYPE_OF`). Supplier and Contact borrowers are no longer possible.

Create opens an unsaved document for the user to review, save and submit. Preparing or submitting a Borrowing or Borrowing Return does not post another document.

- Stock outgoing: submit the Borrowing, then Create → Pick List. Save, pick and submit it, then Create → Delivery Note. Submit the Delivery Note to issue the goods.
- Asset outgoing: submit the Borrowing, then Create → Asset Movement. Save and submit the movement to issue the assets. A Stock Delivery Note does not submit an Asset Movement.
- Incoming: Create → Borrowing Return from the active borrowing, review the returning items, save and submit it. Then Create → Receiving Delivery Note (Stock) or Asset Movement (Asset). Use the Stock User workflow action Receive Borrowing Return on the receiving Delivery Note. Only submission of that receiving document records the physical receipt. Stock requiring inspection enters Quarantine and retains its normal inspection checks. Asset return inspections and holds start only after the receiving Asset Movement is submitted.
- Different returned batches: use Create → Batch Exchange on the submitted Return. Review, save and submit each two-row Repack, repeating until all exchanges are submitted, then submit the receiving Delivery Note. Each exchange preserves one Item's value and links to its Return. No Repack posts automatically.

- Write-offs: create, review and submit the Borrowing Adjustment. It reserves its quantities as Pending Issue. Create → Stock Entry opens its unsaved Material Issue; save and submit it to post the write-off. Cancellation restores the pending reservation until the Adjustment is cancelled.
- Billing: Create → Asset Sale Invoice and Create → Consumption Invoice open unsaved invoices for the accounting user to review, save and submit through normal invoice approvals. Consumption billing requires the write-off Stock Entry to be submitted.

Source and movement documents link in both directions. Server validation locks the borrowing and permits only one active Pick List, Delivery Note or Asset Movement for each source; row quantities, items, warehouses and company must match the saved source. Pending returns and write-offs reserve quantities, and a return's receiving document excludes its own reservation when rechecking availability.

Use native Cancel on movement documents. Cancel dependent returns, adjustments, inspections and shipments first where the native link checks require it. Cancelling the owning Borrowing or Return retains its existing guarded reversal of its own documents and inspections. Delete a draft or cancelled movement through the native Delete action: its own source and Material Request row links are cleared transactionally, while unrelated linked documents still block deletion. Cancel a receiving Delivery Note before cancelling its batch exchanges.

## A Borrow unit becomes a Demo Asset (Siwat, 16 Nov 2026)

An employee who borrowed stock (Material Request "Customer Borrow / Loan", Stock Custody Loan) and places a unit with a
customer for a demo has it converted to a Demo Asset without returning it first:

- The Stock to Asset Conversion names the loan and its row (`custody_loan`, `custody_loan_item`, read only, hidden
  while empty) and never a Material Request (a conversion that names both is refused). It converts one loan row, from
  the loan's Loan warehouse, and only units the borrower still holds: the quantity is within
  `loan_item_available_to_adjust` (excluding itself) and the serial numbers are open and not reserved. Only a loan row
  issued in its stock unit (issue Delivery Note conversion factor 1, `loan_issue_factor`) converts, so the conversion's
  stock quantity and the loan's quantity are the same unit.
- `custody_loan_core.loan_conversion_rows` reads these conversions: a Draft that is not Rejected reserves the units
  (`get_loan_item_reserved_qty`, `get_reserved_serials`), so they cannot be returned, written off or converted twice
  (the tracking table shows "Pending Conversion");
  a submitted conversion settles them like an adjustment (`get_loan_item_balances`, `get_adjusted_serials`; the
  tracking table shows "Converted to Asset"); a cancelled or deleted one counts for nothing. Submitting or cancelling
  the conversion refreshes the loan's status.
- `Custody Loan.material_request_type` (hidden, fetched from the request) lets the form tell a Borrow loan from a
  Customer Demo loan.
- `warehouse_policy.conversion_matches_loan` makes such a conversion a loan movement, the only way stock leaves a Loan
  warehouse into an Asset.
- The generated Assets get the borrower (Employee) as custodian, so the Asset's own receipt places them with her; the
  location is the Loan warehouse's `asset_location` (the conversion's "location required" refusal applies without it).
  An Immediate expense treatment is allowed as for a Customer Demo conversion.
- The Asset Custody Loan that records them afterwards names the conversion (`Custody Loan.source_conversion`): it lists
  exactly the conversion's Assets, is for the same borrower and request, starts Active, counts as issued at once, asks
  for no outgoing Asset Movement (`prepare_asset_movement` refuses) and is left out of the request's fulfilment
  (`update_material_request_status`, `Material Request.update_custody_loan_progress`). Its return is the normal Asset
  return back to the Asset's location.
- The Customer chosen at Convert to Demo Asset is stored in the conversion's read-only `demo_customer` (hidden while
  empty). The Asset loan made from the conversion takes it as `Custody Loan.recipient_customer` ("Recipient Customer",
  editable while draft) with the customer's primary Address and Contact as recipient address and contact, and keeps
  them (`validate_material_request` does not copy the request's address over them); the form filters the address and
  contact by the Recipient Customer. The borrower stays the Sales Employee.
- A submitted conversion is not cancelled while such a loan is submitted, and a submitted Custody Loan of an Asset is a
  follow-up transaction of the conversion.
