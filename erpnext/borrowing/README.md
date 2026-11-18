# Borrowing document flow

The module Borrowing (formerly Loan; the DocTypes Borrowing ... were renamed Borrowing ... on 16 Nov 2026, patch `patches/v16_0/rename_borrowing_to_borrowing.py`) holds what a Material Request lends out. Three request types lend:

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

Source and movement documents link in both directions. Server validation locks the borrowing and permits only one active Pick List, Delivery Note or Asset Movement for each source; row quantities, items, warehouses and company must match the saved source. The issue Delivery Note delivers each Pick List row's warehouse, Storage Location, batch and serial nos as picked (`erpnext/stock/doctype/pick_list/pick_identity.pick_mismatch`, the rule the app's Delivery Note check uses too; those fields are read-only on a Delivery Note row made from a Pick List). Pending returns and write-offs reserve quantities, and a return's receiving document excludes its own reservation when rechecking availability.

Use native Cancel on movement documents. Cancel dependent returns, adjustments, inspections and shipments first where the native link checks require it. Cancelling the owning Borrowing or Return retains its existing guarded reversal of its own documents and inspections. Delete a draft or cancelled movement through the native Delete action: its own source and Material Request row links are cleared transactionally, while unrelated linked documents still block deletion. Cancel a receiving Delivery Note before cancelling its batch exchanges.

## A Borrow unit becomes a Demo Asset (Siwat, 16 Nov 2026)

An employee who borrowed stock (Material Request "Borrow", Stock Borrowing) and places a unit with a
customer for a demo has it converted to a Demo Asset without returning it first:

- The Stock to Asset Conversion names the borrowing and its row (`borrowing`, `borrowing_item`, read only, hidden
  while empty) and never a Material Request (a conversion that names both is refused). It converts one borrowing row, from
  the borrowing's Borrowed warehouse, and only units the borrower still holds: the quantity is within
  `loan_item_available_to_adjust` (excluding itself) and the serial numbers are open and not reserved. Only a borrowing row
  issued in its stock unit (issue Delivery Note conversion factor 1, `loan_issue_factor`) converts, so the conversion's
  stock quantity and the borrowing's quantity are the same unit.
- `borrowing_core.loan_conversion_rows` reads these conversions: a Draft that is not Rejected reserves the units
  (`get_loan_item_reserved_qty`, `get_reserved_serials`), so they cannot be returned, written off or converted twice
  (the tracking table shows "Pending Conversion");
  a submitted conversion settles them like an adjustment (`get_loan_item_balances`, `get_adjusted_serials`; the
  tracking table shows "Converted to Asset"); a cancelled or deleted one counts for nothing. Submitting or cancelling
  the conversion refreshes the borrowing's status.
- `Borrowing.material_request_type` (hidden, fetched from the request) lets the form tell a Borrowing from a
  Customer Demo borrowing.
- `warehouse_policy.conversion_matches_loan` makes such a conversion a borrowing movement, the only way stock leaves a Loan
  warehouse into an Asset.
- The generated Assets get the borrower (Employee) as custodian, so the Asset's own receipt places them with her; the
  location is the Borrowed warehouse's `asset_location` (the conversion's "location required" refusal applies without it).
  An Immediate expense treatment is allowed as for a Customer Demo conversion.
- The Asset Borrowing that records them afterwards names the conversion (`Borrowing.source_conversion`): it lists
  exactly the conversion's Assets, is for the same borrower and request, starts Active, counts as issued at once, asks
  for no outgoing Asset Movement (`prepare_asset_movement` refuses) and is left out of the request's fulfilment
  (`update_material_request_status`, `Material Request.update_borrowing_progress`). Its return is the normal Asset
  return back to the Asset's location.
- The Customer chosen at Convert to Demo Asset is stored in the conversion's read-only `demo_customer` (hidden while
  empty). The Asset Borrowing made from the conversion takes it as `Borrowing.recipient_customer` ("Recipient Customer",
  editable while draft) with the customer's primary Address and Contact as recipient address and contact, and keeps
  them (`validate_material_request` does not copy the request's address over them); the form filters the address and
  contact by the Recipient Customer. The borrower stays the Sales Employee.
- A submitted conversion is not cancelled while such a borrowing is submitted, and a submitted Borrowing of an Asset is a
  follow-up transaction of the conversion.

## The Borrowing Delivery Note is its own kind (Connect Diagnostics, UX WP3, 18 Nov 2026)

A Delivery Note with `is_borrowing_transaction` is not a sale. Its form (`stock/doctype/delivery_note`, JSON
`depends_on` and `delivery_note.js` `set_item_columns`) hides the price list, pricing rules, taxes, totals, discount,
sales team, terms, UTM, auto repeat, Customer PO, the internal customer and its addresses, and shows the section
**Borrowing**: the Borrowing and read-only fetches of its kind (`material_request_type`), borrower type and borrower,
responsible employee, recipient, location, address, contact and custody warehouse. The rows show item, quantity,
warehouse, Storage Location, batch, serial and the target warehouse (read-only); a return names the place
"Receiving Storage Location". `_make_delivery_note` and the return maker set `ignore_pricing_rule` and copy the
recipient address and contact (`set_borrowing_recipient`). `DeliveryNote.validate` calls
`prepare_borrowing_delivery_note` (a selling value typed into a saved one is refused; pricing rules and automatic taxes
off) and `clear_borrowing_selling_fields` (what ERPNext filled by itself is emptied and the totals computed again).
`get_status` never gives To Bill / Partially Billed (Completed instead); the list indicator and the non-billed reports
follow; `make_delivery_trip`, `make_installation_note`, `make_packing_slip` and
`make_inter_company_purchase_receipt` refuse it (`make_sales_invoice` and `make_sales_return` already did).
`issue_credit_note` is hidden on every Delivery Note, and `return_reason` is required where it shows (a sales return).
Tests: `borrowing/doctype/borrowing/test_borrowing_delivery_note_kind.py` (offline).

## Navigation names

The Workspace, Sidebar, Workspace Sidebar, Desktop Icon and dock entry of this module are named **Borrowings**. The
desk router resolves a workspace slug before a DocType route, so a workspace named "Borrowing" would take
`/desk/borrowing` and every `/desk/borrowing/<name>` away from the Borrowing list and form. The module itself stays
"Borrowing" (`patches/v16_0/rename_custody_loan_to_borrowing.py` renames Loan or Borrowing records to Borrowings and
moves Block Module rows of Loan to Borrowing).
