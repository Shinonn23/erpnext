"""Stock Count: the Lost fields carry their own names, the retired Found fields go (WP10, 18 Nov 2026; pre-go-live).

``quarantine_warehouse`` and ``quarantine_storage_location`` always held the Lost Warehouse and its location (a
Monthly count moves the quantity it did not find there), while ``Warehouse.quarantine_warehouse`` is the real
Quarantine: the same name meant two things. They become ``lost_warehouse`` / ``lost_storage_location``. The Found
fields (``found_warehouse``, ``found_stock_entry``, ``found_transfer_entry``) belonged to the FOUND receipt and the
recount of surplus that nothing has called since 11 Nov 2026; their columns are dropped while empty (a value is never
discarded: it is logged and the column stays).

Schema only, at the end of [pre_model_sync]; each step is guarded, so the patch can run again.
"""

import frappe
from frappe.model.utils.rename_field import rename_field

DOCTYPE = "Stock Count"
RENAMED = {
	"quarantine_warehouse": "lost_warehouse",
	"quarantine_storage_location": "lost_storage_location",
}
RETIRED = ("found_warehouse", "found_stock_entry", "found_transfer_entry")


def execute():
	if not frappe.db.exists("DocType", DOCTYPE):
		return
	frappe.reload_doc("stock", "doctype", "stock_count")
	for old, new in RENAMED.items():
		if frappe.db.has_column(DOCTYPE, old):
			rename_field(DOCTYPE, old, new)
			# a second run then finds nothing to copy and never overwrites the values written since
			frappe.db.sql_ddl(f"alter table `tab{DOCTYPE}` drop column `{old}`")
	for fieldname in RETIRED:
		frappe.db.delete("Property Setter", {"doc_type": DOCTYPE, "field_name": fieldname})
		if not frappe.db.has_column(DOCTYPE, fieldname):
			continue
		# raw SQL: the field has left the DocType's meta
		if frappe.db.sql(f"select 1 from `tab{DOCTYPE}` where coalesce(`{fieldname}`, '') != '' limit 1"):
			frappe.log_error(
				title="Stock Count field kept", message=f"{DOCTYPE}.{fieldname} still holds values"
			)
			continue
		frappe.db.sql_ddl(f"alter table `tab{DOCTYPE}` drop column `{fieldname}`")
	frappe.clear_cache(doctype=DOCTYPE)
