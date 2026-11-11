# Service Equipment

A machine the service team looks after for its owner (a supplier), installed at a customer or kept in a company
Location. It is not an Asset: it has no value, depreciation, GL Entry, capitalization, stock or cost center.

## The record

`Service Equipment` (module Support, named by `equipment_id`): identity (name, Item, brand, model, serial number,
optional Stock Serial No, Owner Supplier, responsible Company, status, received date), the current place (Location,
custodian, customer, address, placed on, last movement; read only, written by a submitted Asset Movement), the service
plan and cover (technician, Asset Maintenance Team, contract, warranty and contract end, next PM and calibration from
its Asset Maintenance) and the import source. `Service Equipment Settings.supplier` is the default Owner Supplier.
`Service Equipment Check` is the checklist row of a Maintenance Visit, naming the equipment it was read on.

Statuses: Received, Installed, In Service, Under Repair, Out of Order, Returned to Supplier, Retired.

## Shared with Asset

The Asset support DocTypes take either an Asset or a Service Equipment (`service_equipment`); a document about
equipment never takes a financial path. The helpers are `erpnext/support/service_equipment.py`.

| DocType | Asset or equipment | Skipped for equipment |
|---|---|---|
| Asset Movement (+ Item) | each row names `asset` or `service_equipment`; a movement holds one kind only | Asset status / company asset rules, Material Request and custody loan, demo status, Asset location write; the handover fields (type, sender, receiver, reason, condition, optional evidence, destination customer and address) apply. Submit writes the place and status on the equipment; only its latest movement can be cancelled, and cancel restores the place before it (the status only while it is still the one the movement set) |
| Asset Maintenance (+ Task, Log) | `asset_name` or `service_equipment` (an equipment plan is named "<equipment>-SE", never an Asset's name) | Asset status; the tasks, periodicity, next due date, ToDo and logs are ERPNext's. The plan sets the equipment's next PM / calibration. A submitted Maintenance Visit row with type PM or Calibration and result Done completes the open log of that type due soonest |
| Asset Repair | `asset` or `service_equipment` | stock consumption, consumed stock items, capitalization, purchase invoices, GL Entries, depreciation, cost center and the value fields (zero). Parts Used (`Service Equipment Repair Part`: item, serial no, qty, note) and Repair Cost (record) are notes only. A draft changes nothing on the equipment and Retired or Returned to Supplier equipment is not repaired. Submitted while Pending, the repair is open and the equipment is Under Repair; completing it (Repair Status, after submit) or cancelling it brings back the status it had before, unless something else changed it meanwhile. Accounting has no right on an equipment repair; the service team and System Manager act on it |
| Asset Activity | `asset` or `service_equipment` | none: movements, repairs, maintenance logs, visits, Issues, Warranty Claims and Installation Notes add a line |

Issue, Warranty Claim, Maintenance Visit (+ Purpose), Maintenance Schedule (+ Item) and Installation Note Item carry a
standard `service_equipment` Link. A submitted Installation Note whose rows name equipment makes and submits one Install
Asset Movement to the note's customer and address; cancelling the note cancels it, refused while a later movement of
the equipment exists.

A holder of Maintenance User / Maintenance Manager who has no other right on a shared DocType sees and saves only
its equipment records (`permission_query_conditions` and `has_permission` in `hooks.py`). Nobody in accounts has a
right on Service Equipment.

## Reports

Service Equipment Location History, Service Equipment Maintenance Due and Service Equipment Repair History (Support).
The Support sidebar and workspace hold the group "Service Equipment" and its reports.
