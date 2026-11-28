# Borrowing document flow

The module Borrowing (formerly Loan; the DocTypes Borrowing ... were renamed Borrowing ... on 16 Nov 2026, patch `patches/v16_0/rename_borrowing_to_borrowing.py`) holds what a Material Request lends out. Three request types lend:

- **Borrow**: an Employee borrows and holds the goods. The borrower is always an Employee.
- **Customer Borrow**: a Customer borrows to use and returns (for example a laboratory reagent). The borrower is always a Customer.
- **Customer Demo**: a demonstration at a Customer, unchanged.

The request's Borrower Type is derived from its type (read only), and a Borrowing refuses a borrower of another type than its request names (`BORROWER_TYPE_OF`). Supplier and Contact borrowers are no longer possible.

## The chain (UX WP4, Siwat 18 Nov 2026: "ทำไมไม่ทำเหมือนงานขาย SO -> DN ... เอกสารควรสร้างต่อกันเป็นทอดๆ")

A borrowing is made one document from the one before it with Create, as in selling (Quotation -> Sales Order -> Pick
List -> Delivery Note -> return Delivery Note made from that Delivery Note). Create opens an unsaved document for the
user to review, save and submit; preparing or submitting a Borrowing never posts another document.

- **Stock**: Material Request (Borrow / Customer Borrow / Customer Demo) -> Borrowing (the "order") -> Create > Pick
  List -> Create > Delivery Note (the issue, into the Borrowed warehouse) -> **Create > Return on the issue Delivery
  Note** (or Create > Return on the Borrowing): the return Delivery Note (`is_return`, `return_against` = the issue note,
  `is_borrowing_transaction`, `borrowing`, source the Borrowing; `make_borrowing_return_delivery_note`, also what the
  native `make_sales_return` opens for a borrowing note). It lists only the units still with the borrower, one row per
  issued row and batch, back into the warehouse each was lent from; a row whose Item is inspected on receipt comes back
  into the quarantine of that warehouse (`receive_row_into_quarantine`). Submitting it receives the goods.
- **Asset**: Borrowing -> Create > Asset Movement (the issue) -> **Create > Return Movement on the issue movement** (or
  Create > Return on the Borrowing): the return Asset Movement (purpose Receipt, `return_against` = the issue movement,
  `make_borrowing_return_movement`) takes the Assets still with the borrower back to the Location each left.
- With several issue documents, Create > Return on the Borrowing asks which one to return against.
- Write-offs: create, review and submit the Borrowing Adjustment from the Borrowing. It reserves its quantities as
  Pending Issue. Create > Stock Entry opens its unsaved Material Issue; save and submit it to post the write-off.
  Cancellation restores the pending reservation until the Adjustment is cancelled.
- Convert to Demo Asset stays (below). Billing: Create > Asset Sale Invoice and Create > Consumption Invoice open unsaved
  invoices for accounting, through normal invoice approvals; consumption billing requires the write-off Stock Entry.

For an Employee Stock Borrowing, Create > Demo Consumption and My Demo Consumables use the same confirmation.
The borrower selects eligible items, a customer, and a demo date. The confirmation submits a Borrowing Adjustment
and posts its Material Issue using the company's demo expense account and the site's active Stock Entry workflow.
Repeating the same confirmation returns the existing documents once both are submitted.

There is no Borrowing Return document, no "Receiving Delivery Note" step and no Batch Exchange: the DocTypes Borrowing
Return and Borrowing Return Item are removed (`patches/v16_0/remove_borrowing_return.py`, schema only; a site with
records keeps them until the Connect Diagnostics seed step that settles them, `e2e/data_access/borrowing_return_chain`,
has linked their receiving documents to the Borrowing and deleted them).

### What a return may take

- Balances (`get_loan_item_balances`, `get_loan_item_reserved_qty`, `borrowing_tracking`, the reports): issued = the
  submitted issue Delivery Note / Asset Movement, returned = the submitted return Delivery Notes / return movements
  (`return_documents`, `return_rows`), pending return = their drafts (they reserve the units, as a draft write-off does),
  adjusted = the Borrowing Adjustments, converted = the Stock to Asset Conversions.
- A return takes only units still with the borrower: more than is out, per Item, batch and serial number, is refused
  (`validate_borrowing_return_delivery_note`, `validate_borrowing_return_movement`). A serial number comes back as the
  very unit that was lent.
- **Another batch of the same Item** may be typed on a return row (Siwat, 5 Nov 2026). It is valued at the issued row's
  rate (`serial_and_batch_bundle.is_borrowing_return`) and tracked as a swap in the Serial / Batch Tracking table
  (`returned_as`). Submitting the return posts, for each such row, a value-preserving Repack inside the Borrowed
  warehouse (lent batch out, returned batch in; `post_return_batch_swaps`, linked by `delivery_note_no`) so the return
  takes the returned batch out of the Borrowed warehouse; cancelling the return cancels it
  (`cancel_return_batch_swaps`). Nobody makes or cancels that Repack by hand (`validate_batch_swap`).

### Links and cancellation

Source and movement documents link in both directions; the Borrowing's Connections show its request, Pick List, issue
and return documents, write-offs, sales and inspections. Server validation locks the borrowing and permits only one
active Pick List, issue Delivery Note or issue Asset Movement for each borrowing; row quantities, items, warehouses and
company must match the saved source. The issue Delivery Note delivers each Pick List row's warehouse, Storage Location,
batch and serial nos as picked (`erpnext/stock/doctype/pick_list/pick_identity.pick_mismatch`).

Use native Cancel on movement documents: cancel the return documents and write-offs before the issue document, the
issue document before the Borrowing. Delete a draft movement through the native Delete action: its own source and
Material Request row links are cleared transactionally.

### Columns by type

The Borrowing Items grid shows Source Warehouse, Batch and Serial on a Stock borrowing and Asset and Recipient Location
on an Asset borrowing (`borrowing.js` `configure_item_columns`); the server clears the fields of the other type and
keeps an Asset row at quantity 1, and a Source Warehouse must allow lending under the warehouse operation policy. A
Quarantine warehouse is recognised by its Warehouse Type only (`is_quarantine_warehouse`).

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
recipient address and contact (`set_borrowing_recipient`); the native party address / contact check is replaced by
`validate_borrowing_address_and_contact` (they belong to the internal customer, the recipient customer or the
borrower). `DeliveryNote.validate` calls
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
