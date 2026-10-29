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
