"""Delete the Custom Fields left over from the first "Medical Device" version of Regulatory Affairs.

`ensure_custom_fields` only creates and updates the fields it defines, so the retired ones stayed on sites
installed before the rename: Item "Is Medical Device", "Registration Requirement", "Exemption Reason" and a
second "Current Registration", and a second "Regulatory Override" on the six guarded transactions. The two
Links point to DocTypes that no longer exist. The Item "Regulatory Owner" kept `depends_on` on the retired
"Is Medical Device" box. Columns and values stay in the tables; nothing is dropped.
"""

import frappe

RETIRED_FIELDS = {
	"Item": (
		"is_medical_device",
		"regulatory_registration_requirement",
		"regulatory_exemption_reason",
		"current_medical_device_registration",
	),
	**{
		doctype: ("medical_device_regulatory_override",)
		for doctype in (
			"Purchase Order",
			"Purchase Receipt",
			"Stock Entry",
			"Sales Order",
			"Delivery Note",
			"Sales Invoice",
		)
	},
}


def execute():
	for doctype, fieldnames in RETIRED_FIELDS.items():
		for name in frappe.get_all(
			"Custom Field", filters={"dt": doctype, "fieldname": ("in", fieldnames)}, pluck="name"
		):
			frappe.delete_doc("Custom Field", name, ignore_missing=True, force=True)
		frappe.clear_cache(doctype=doctype)
	owner = frappe.db.get_value("Custom Field", {"dt": "Item", "fieldname": "regulatory_owner"}, "name")
	if owner and frappe.db.get_value("Custom Field", owner, "depends_on"):
		frappe.db.set_value("Custom Field", owner, "depends_on", None, update_modified=False)
		frappe.clear_cache(doctype="Item")
