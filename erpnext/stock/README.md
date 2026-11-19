Inventory management module.

## Stock Count: choosing the items

A Monthly Stock Count counts either a random sample (Item Selection = Random Sample, the default; Number of
Item Codes to Sample) or the items you choose (Selected Items). An Annual count always counts every item with
stock and has no selection.

- **Items to Count** (Selected Items only): the chosen enabled stock items, each once. At Prepare Count only the
  chosen items that have stock in the warehouse (and in the Inventory Dimension filters) get count rows; the
  others are listed in the message "Not counted, no stock in ... for the chosen filters". If none has stock, the count is not prepared.
- **Count Template** (Stock Count Template: the title is its name, company, optional warehouse, items): choosing one fills Items
  to Count in Draft. The items are copied; editing the template later does not change a prepared count. Rows
  chosen by hand are never overwritten; an empty list is filled from the template on the server at every save.
- **Save as Template** (Draft button): saves the chosen items as a new template of the count's Company and
  Warehouse (needs create right on Stock Count Template; Stock User and Stock Manager have it).
- The selection can change only before Prepare Count. The print format and list show the mode and template.
- Where the Warehouse has a Count Variance Account (field `stock_adjustment_account`, added by the company
  policy layer; absent here), a Monthly count posts its differences as a Stock Reconciliation on submit instead
  of the Lost Stock Entry (the Lost Warehouse is then optional). The mode is fixed at Prepare Count, the
  account must be an enabled, non-Stock expense account of the Company and becomes the Difference Account, and a
  Warehouse `department` (if that field exists) becomes the department. Without that field or value nothing changes.
  A Warehouse that names its own Lost Warehouse (below) never posts its differences that way: the shortage goes to Lost.
- **Lost Warehouse of each counted warehouse** (Connect Diagnostics, 7 Nov 2026): the Warehouse has the link
  `lost_warehouse` (section "Lost and Found"; `found_warehouse` is hidden and unused). A Monthly count moves its shortage
  to the counted warehouse's own Lost Warehouse (Stock Count `lost_warehouse` / `lost_storage_location`); a Warehouse
  without the link keeps the company's Lost warehouse (the first enabled leaf of the Warehouse Type that no other
  warehouse names as its own). The Stock Count form picks the default again when the warehouse changes. The count never
  receives what it found beyond the system quantity (11 Nov 2026): WP10 (18 Nov 2026) renamed the count's fields from
  `quarantine_warehouse` / `quarantine_storage_location` (they always held the Lost Warehouse, while
  `Warehouse.quarantine_warehouse` is the real Quarantine) and deleted the Found fields `found_warehouse`,
  `found_stock_entry`, `found_transfer_entry` with the code nothing called (`handle_surplus`, `make_recount`, the FOUND
  receipt and transfer, `take_back_from_lost`): patch `v16_0/rename_stock_count_lost_fields` (pre-model-sync, schema
  only; a Found column that still holds a value is kept and logged). The recount fields `recount_of` /
  `recount_stock_count` stay for the counts that have them; no new recount is made.
- **Quarantine Warehouse of each warehouse** (Connect Diagnostics, 7 Nov 2026): the Warehouse has the link
  `quarantine_warehouse` (same section). Goods that wait for a Quality Inspection or fail one are held in the quarantine
  of the warehouse they belong to: a rejected sales or borrowing return is received into the quarantine of the warehouse
  the goods came from (`borrowing_core.quarantine_warehouse_for(company, near)`: the own link when it names an
  enabled leaf of the company, else the company's Quarantine warehouse that no warehouse names as its own, the one
  beside `near` first). Every test "is this a quarantine warehouse" is the Warehouse Type Quarantine alone
  (`warehouse_policy.is_quarantine_type`, used by `is_quarantine_warehouse` and `is_quarantine_row`; the retired alias
  `QA` and the test on the warehouse's name are gone, WP10).
- **Damage Warehouse of each warehouse** (`damage_warehouse`, same section; a field of this JSON since WP10, a Custom
  Field of the app before): where goods rejected by a Quality Inspection go. The three links (`lost_warehouse`,
  `quarantine_warehouse`, `damage_warehouse`) show on a Stock warehouse only (`depends_on`
  `!doc.is_group && doc.warehouse_type=='Stock'`, or while the field holds a value); the Damage Warehouse also on a
  Quarantine warehouse, the one exception (rejected goods go straight from quarantine to damage). The app refuses the
  links on any other source type. A Stock Count reads the ledger of its one leaf warehouse, so the quarantine (or Lost, or
  Found) warehouse of the counted warehouse is never part of its count.
- **Stock Write-Off**: a Stock Entry of the Stock Entry Type "Stock Write-Off" (Material Issue) may leave a warehouse of
  Warehouse Type Lost, which otherwise allows no issue and lets only the Stock Manager move stock out
  (`warehouse_policy.is_write_off_from_lost`). The company policy layer refuses the type from any other warehouse.

## Stock UX review (Connect Diagnostics, 6 Nov 2026)

- **Stock Count**: the Lost Warehouse is read-only; the form fills it from the counted warehouse's or the company's LOST
  warehouse (`get_loss_warehouses`) and the server still fills a blank on save. A failed scan names the code and the
  location. An approver's state shows how many rows are short and over (units only, never money) and opens the Review
  tab first. The first counter of a recount sees at once that another person must count it.
- **Stock Reconciliation made by a Stock Count**: rows, Difference Account and Department are read-only, Add row / Upload /
  Fetch Items from Warehouse are hidden, and the server refuses a changed item, warehouse, storage location, batch or
  quantity (`validate_stock_count_rows_kept`; `flags.allow_stock_count_row_changes` lifts it for code). Its Department is
  the counted Warehouse's department; a Warehouse without one keeps the creator's.
- **Storage Location**: a blank Location Code is built from Zone-Rack-Shelf.
- **Stock Ledger Entry**: the inventory-dimension shortage reads "Only N of ITEM at LOCATION on DATE; M more needed".
- **Warehouse**: the Warehouse Type intro is one line (the groups of operations), not every document name. Since WP10
  it ends with the code waivers of the type ("Exceptions: ..."), so the form tells the whole rule (below).
- **Item**: the Regulatory Affairs headline is cleared before it is set, so it shows once.

## Warehouse policy in one place (Connect Diagnostics, WP10, 18 Nov 2026)

**The Warehouse Type says what the stock is; the Location Type says what the bin is for.** "May stock leave or be sold
from here" has one answer, built only from the Warehouse Type flags (`allow_receipt`, `allow_issue`, `allow_transfer`,
`allow_sales`, `allow_loan`, `allow_reconciliation`), never from a list of types or a warehouse's name:

- `warehouse_policy.warehouse_allows(warehouse, operation)`: the type's flag for the operation (no type allows all; a
  group or unknown warehouse nothing; a Borrowing movement of a Transit warehouse counts as a transfer).
- `warehouse_policy.is_sellable_warehouse(warehouse)` and its SQL twin `sellable_warehouse_sql(alias)`: the type allows a
  sale or an issue (`SELLABLE_OPERATIONS`). Only Stock does on the company's sites. Used by
  `borrowing_core.stocked_warehouse` (the default source of a Borrowing: never a Lost, Found, Write-Off, Frozen, Transit,
  quarantine or other Borrowed warehouse) and by the app (picking, put-away, the stock age report).
- `warehouse_policy.is_quarantine_type(warehouse_type)` / `warehouse_is_quarantine(warehouse)`: the Quarantine type alone.
- The exits the code opens for single documents are listed in `CODE_WAIVERS` (Stock Write-Off out of Lost, Disposal out
  of Write-Off, the Purchase Return of rejected goods out of Quarantine, the Stock Count's own posting) and an app adds
  its own with the hook `warehouse_policy_waivers` (dicts `warehouse_type`, `lifts`, `text`; the app lists the
  Quarantine Release). `get_warehouse_type_summary` returns them as `waivers` and the Warehouse form shows them. A child
  table "Allowed exits" on Warehouse Type that `validate_warehouse_operation` reads is a later step.

## Storage Location: Location Type, and the screen shows what the save stores (Connect Diagnostics, WP1, 18 Nov 2026)

The Warehouse Type says what the stock is (Stock, Quarantine, Lost, Found, Borrowed, Write-Off, Frozen, Transit); the
**Location Type** of a Storage Location (`location_type`, required, default Storage) says what the bin is for:
**Receiving** (goods land here and leave only by a Stock Entry), **Storage** (shelves goods are picked from), **Sales
Counter** (where a lab sells from) and **Holding** (the one bin of a holding warehouse). The code of a location
(`STAGING`, `LAB MACHINE`) no longer decides anything. A new warehouse's default location (`ensure_default_storage_location`)
is Receiving in a stock warehouse, Holding in any other.

- One rule, the same in Python and SQL: `storage_location.is_receiving_location(name, warehouse=None)` and
  `receiving_location_sql(expression, warehouse_expression=None)` (enabled, type Receiving; `test_receiving_location`
  runs both on the same rows).
- `refuses_receiving_source(doc, row)`: a non-return Delivery Note, a Sales / POS Invoice with Update Stock, a Stock to
  Asset Conversion, a Material Issue and the source of a transfer to another stock warehouse may not take goods from a
  Receiving location. `StockController.validate_storage_locations` refuses such a row (or a bundle entry there): "Row
  N: X is at the receiving location L; put it away with a Stock Entry first, or choose the Storage Location", and at the
  submit an outgoing stock row with no location ("Row N: choose the Storage Location ..."). A put-away inside one
  warehouse and a move into a holding warehouse may take from it. The Pick List never picks it.
- `set_default_storage_locations` (before_validate) fills only what the user would not choose otherwise: an outgoing
  row takes the one location that holds its stock (`source_storage_location`, Receiving stock not counted for the rows
  above), else stays empty, never the Receiving default of a stock warehouse (a holding warehouse keeps its one bin); a
  row of a non-stock Item gets none and `validate_storage_locations` clears what it carries; the target of a Stock
  Entry transfer into a stock warehouse stays empty (put-away, move out of quarantine, normal warehouse), except into a
  warehouse with a Sales Counter location (a lab): the Item's preferred non-Receiving location there, else the Sales
  Counter (`transfer_target_location`; only a Material Transfer: a transfer for manufacture or to a subcontractor
  keeps the target's default); an incoming leg gets the warehouse default. The source of a POS Invoice row of a
  register (`pos_profile`) is left empty and never computed: the Connect Diagnostics app sets the register's Sales
  Storage Location after this hook, and a cashier cannot choose a location on the POS page.
- `storage_location_query` takes `exclude_receiving`: `1` never offers a Receiving location, `stock` not when the
  field's warehouse is a stock warehouse, `target` not when `target_warehouse` is one. `public/js/storage_location.js`
  passes it on every source field of a sale, conversion or Material Issue (not on a return, a Repack or a Manufacture) and on a transfer's source and
  target (`erpnext.storage_location_exclude_receiving`), and also serves POS Invoice and Stock to Asset Conversion.
  The Pick List keeps only the app's picker query (the fork's duplicate `set_query` is gone).
- Delivery Note Item, Purchase Receipt Item, Purchase Invoice Item, Stock Entry Detail, Packed Item and Stock to Asset
  Conversion Item fetch a hidden `is_stock_item`; their location fields show only for a stock Item (or when they hold a
  value). Stock Entry Detail fetches `t_warehouse_type`, and its To Storage Location is mandatory on a Material Transfer
  into a stock warehouse. Stock to Asset Conversion Item shows its Storage Location as a column.

## Fixed-asset receipt rows and the bin of a quarantined row (Connect Diagnostics, WP6, 19 Nov 2026)

- Purchase Receipt Item and Purchase Invoice Item: a fixed-asset row (`is_fixed_asset`) hides Storage Location,
  Rejected Quantity, Rejected Warehouse and Rejected Storage Location (`depends_on eval:!doc.is_fixed_asset`; Rejected
  Storage Location also only with a Rejected Warehouse), and its Asset Location is `mandatory_depends_on` (a Purchase
  Receipt that is not a return; a Purchase Invoice with Update Stock that is not a return). The server checks the same on
  save: `BuyingController.validate_asset_locations` ("Row #N: enter the Asset Location of the asset item X"), not only
  when `make_asset` makes the Asset at submit.
- A row the Connect Diagnostics app received into Quarantine (`putaway_warehouse`, "Receive for Warehouse") keeps the bin
  the user chose before in the no_copy, read-only `putaway_storage_location` ("Receive for Storage Location"), and its
  Storage Location is read-only (`read_only_depends_on eval:doc.putaway_warehouse`).
  `purchase_inspection_units.build_release_entry` sends accepted goods there (`_putaway_location`: a non-Receiving
  location of the target warehouse), else to the Item's preferred location; rejected goods never.
- The header `set_warehouse` of Purchase Receipt and Purchase Invoice is labelled "Receive for Warehouse", and
  `TransactionBase.reset_default_field_value` reads a row's `putaway_warehouse` for it, so a receipt whose rows are held
  in Quarantine keeps the warehouse they are for.
- Offline tests: `stock/doctype/purchase_receipt/test_receipt_asset_rows.py`.

## Delivery Note by kind (Connect Diagnostics, UX WP3, 18 Nov 2026)

A Borrowing / Demo Delivery Note has its own layout, status and makers: see `borrowing/README.md` "The Borrowing
Delivery Note is its own kind". `accounts/report/non_billed_report.py` leaves it out of "Delivered Items To Be
Billed", and `delivery_note_list.js` never shows it To Bill and refuses it in the bulk Sales Invoice action.
