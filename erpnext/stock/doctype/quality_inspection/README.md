Incoming quality inspection for received Items.

`Inspected By` defaults to the current authenticated user through the native `__user` token.
Both Desk and server-created documents resolve it before Link validation; it must never become a literal
User named `user`. This default supplies identity only and does not replace team, Item-assignment or workflow checks.

A borrowing's return Asset Movement (UX WP4) is inspected on receipt. Its rows are the `assets` table and name the Item
`asset_item_code`, so `set_child_row_reference` picks the first uninspected row of the Item
(`asset_movement_row_for`) and the Item search lists the uninspected Items of the movement (`asset_movement_items`);
`update_qc_reference` writes only the inspected row; the native `<reference_type> Item.item_code` queries are not used for it.
