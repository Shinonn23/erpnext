# Asset Movement (Connect Diagnostics fork)

## Row fields by purpose (UX WP9, 19 Nov 2026)

What a purpose shows is what it stores (`asset_movement.js` `MOVEMENT_FIELDS_BY_PURPOSE`, the `depends_on` of
`asset_movement_item.json`, `AssetMovement.clear_hidden_fields`):

| Purpose | Row fields |
|---|---|
| Transfer, Transfer and Issue | Source and Target Location, From Employee, To Employee |
| Receipt | Source Location (where the asset was, read only), Target Location, From Employee, To Employee |
| Issue | From Employee, To Employee (no location) |
| Reassignment | From / To Employee, Change Custodian, Change Department, Source / Target Department (no location) |

- Changing the purpose on the form empties the locations the new purpose hides and the Reassignment choices, and
  brings the asset's current location back when the new purpose shows the source again.
- The server clears the same fields on save: an Issue or a Reassignment stores no Source or Target Location, so a
  place typed under an earlier purpose never becomes the Asset's location (the latest movement's target is read as
  the Asset's place); outside a Reassignment Change Custodian / Change Department are 0 and Target Department empty.
- From Employee is the custodian the system writes from the Asset on every purpose, so it is shown on every purpose
  (read only except Transfer and Issue); a Receipt keeps its Source Location (the employee return of the app reads
  both).
- Service Equipment rows and the rows of a borrowing (written by `borrowing_core`) are left as the system wrote them.
