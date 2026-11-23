Stock Count records all system quantities, counted quantities and differences in the Item's Stock UOM.
The Stock UOM is shown on each row, including existing counts.

Use the header action **Count in UOM** to select an existing row and enter a physical quantity in any UOM
configured on that Item. Apply the first quantity, then select **Add to Existing Count** to add another unit.
For example, with Bottle = 720 Tests, two bottles plus three Tests save a Counted Qty of 1,443 Tests.
The saved count and all resulting stock documents use the Stock UOM.

Each barcode scan adds one Stock UOM. Count packaged goods through Count in UOM.
Serial numbers must have total counts of zero or one. Whole-number stock UOMs reject fractional totals.
Batch and storage-location rows remain separate; choose the matching row before applying a count.

## Lost Storage Location

A Monthly count excludes stock already in a Lost Storage Location from its snapshot. Shortages move to the item-compatible
Lost location in the counted warehouse, keeping the stock in that warehouse while separating it from ordinary stock. If
the counted warehouse has no Lost location, the count falls back to an active Lost Warehouse and optional Holding location.
The fields `lost_warehouse` and `lost_storage_location` retain the legacy destination where one was already selected. They
were named `quarantine_warehouse` / `quarantine_storage_location` before, although they never held a quarantine; the
pre-model-sync patch `v16_0/rename_stock_count_lost_fields` renames them. The count never receives a surplus (11 Nov
2026): the Found fields (`found_warehouse`, `found_stock_entry`, `found_transfer_entry`) and the code that made the FOUND
receipt, the transfer back from Lost and the recount (`handle_surplus`, `make_recount`, `make_found_receipt`,
`make_found_transfer`, `take_back_from_lost`, `lost_balance`, `found_valuation_rate`, `get_found_warehouse`) are
deleted. The surplus stays on the approved count and is listed by the app's report Stock Count Surplus.
