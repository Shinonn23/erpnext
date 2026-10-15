# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe


def execute():
	count_item = frappe.qb.DocType("Stock Count Item")
	batch = frappe.qb.DocType("Batch")
	# Legacy counts have no expiry snapshot; use the current batch date once during migration.
	(
		frappe.qb.update(count_item)
		.set(
			"expiry_date",
			frappe.qb.terms.Bracket(
				frappe.qb.from_(batch).select(batch.expiry_date).where(batch.name == count_item.batch_no)
			),
		)
		.where(count_item.expiry_date.isnull())
		.where(count_item.batch_no.isnotnull())
	).run()

	frappe.db.set_value(
		"Custom DocPerm",
		{
			"parent": "Stock Count",
			"role": ["in", ["Stock User", "Stock Manager", "Accounts Manager"]],
			"permlevel": 0,
			"read": 1,
		},
		"print",
		1,
		update_modified=False,
	)
	frappe.clear_cache(doctype="Stock Count")
