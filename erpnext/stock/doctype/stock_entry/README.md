# Stock Entry (Connect Diagnostics fork)

## Purpose-specific fields (UX WP9, 19 Nov 2026)

- Items grid: the Storage Location columns follow their warehouse columns (`toggle_warehouse_fields`, run on the
  Stock Entry Type and the purpose). A purpose with no source (Material Receipt, Receive from Customer) has no
  Source Warehouse and no Storage Location column; one with no target (Material Issue, the Stock Write-Off included)
  has no Target Warehouse and no Target Storage Location column.
- Issue Type (`operation_issue_type`): Internal Use, Maintenance Spare Parts or Disposal, shown on a Material Issue
  only. It has no JSON default (the first option is blank): the app sets it on a Material Issue and clears it on
  every other purpose (`connect_diagnostics_setup.workflows.operation_plan.normalize_purpose_fields`). The option
  Demo is gone: a demo goes out only through a Material Request Customer Demo and its Borrowing.
- A Stock Write-Off is nobody's request: Requesting Employee, Requesting Department, Issue Type and their section
  are hidden for the type by `depends_on`, and the app stores none of them (`stock/write_off.py`).
