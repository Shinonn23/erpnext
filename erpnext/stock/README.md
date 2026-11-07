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
  of the quarantine Stock Entry (the Quarantine Warehouse is then optional). The mode is fixed at Prepare Count, the
  account must be an enabled, non-Stock expense account of the Company and becomes the Difference Account, and a
  Warehouse `department` (if that field exists) becomes the department. Without that field or value nothing changes.
  A Warehouse that names its own Lost Warehouse (below) never posts its differences that way: the shortage goes to Lost.
- **Lost and Found Warehouse of each counted warehouse** (Connect Diagnostics, 7 Nov 2026): the Warehouse has the links
  `lost_warehouse` and `found_warehouse` (section "Lost and Found"). A Monthly count moves its shortage to the counted
  warehouse's own Lost Warehouse and receives its confirmed surplus into its own Found Warehouse; a Warehouse without the
  link keeps the company's Lost / Found warehouse (the first enabled leaf of the Warehouse Type that no other warehouse
  names as its own). The Stock Count form picks the defaults again when the warehouse changes.
- **Quarantine Warehouse of each warehouse** (Connect Diagnostics, 7 Nov 2026): the Warehouse has the link
  `quarantine_warehouse` (same section). Goods that wait for a Quality Inspection or fail one are held in the quarantine
  of the warehouse they belong to: a rejected sales or loan return is received into the quarantine of the warehouse
  the goods came from (`custody_loan_core.quarantine_warehouse_for(company, near)`: the own link when it names an
  enabled leaf of the company, else the company's Quarantine warehouse that no warehouse names as its own, the one
  beside `near` first). Every test "is this a quarantine warehouse" accepts any warehouse of Warehouse Type Quarantine
  (`is_quarantine_warehouse`). A Stock Count reads the ledger of its one leaf warehouse, so the quarantine (or Lost, or
  Found) warehouse of the counted warehouse is never part of its count.
- **Stock Write-Off**: a Stock Entry of the Stock Entry Type "Stock Write-Off" (Material Issue) may leave a warehouse of
  Warehouse Type Lost, which otherwise allows no issue and lets only the Stock Manager move stock out
  (`warehouse_policy.is_write_off_from_lost`). The company policy layer refuses the type from any other warehouse.

## Stock UX review (Connect Diagnostics, 6 Nov 2026)

- **Stock Count**: the Lost and Found Warehouse are read-only; the form fills them from the company's LOST / FOUND
  warehouses (`get_loss_warehouses`) and the server still fills a blank on save. A failed scan names the code and the
  location. An approver's state shows how many rows are short and over (units only, never money) and opens the Review
  tab first. The first counter of a recount sees at once that another person must count it.
- **Stock Reconciliation made by a Stock Count**: rows, Difference Account and Department are read-only, Add row / Upload /
  Fetch Items from Warehouse are hidden, and the server refuses a changed item, warehouse, storage location, batch or
  quantity (`validate_stock_count_rows_kept`; `flags.allow_stock_count_row_changes` lifts it for code). Its Department is
  the counted Warehouse's department; a Warehouse without one keeps the creator's.
- **Storage Location**: a blank Location Code is built from Zone-Rack-Shelf.
- **Stock Ledger Entry**: the inventory-dimension shortage reads "Only N of ITEM at LOCATION on DATE; M more needed".
- **Warehouse**: the Warehouse Type intro is one line (the groups of operations), not every document name.
- **Item**: the Regulatory Affairs headline is cleared before it is set, so it shows once.
