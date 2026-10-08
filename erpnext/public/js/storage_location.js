// Warehouse-specific queries for the built-in Storage Location inventory dimension.
for (const doctype of [
	"Stock Entry", "Purchase Receipt", "Purchase Invoice", "Delivery Note",
	"Sales Invoice", "Stock Reconciliation", "Subcontracting Receipt",
]) {
	frappe.ui.form.on(doctype, {
		setup(frm) {
			for (const table of frm.meta.fields.filter((df) => df.fieldtype === "Table")) {
				const meta = frappe.get_meta(table.options);
				for (const field of (meta?.fields || []).filter(
					(df) => df.fieldtype === "Link" && df.options === "Storage Location"
				)) {
					frm.set_query(field.fieldname, table.fieldname, (doc, cdt, cdn) => {
						const row = locals[cdt][cdn];
						let warehouse = row.warehouse;
						if (field.fieldname.startsWith("rejected_")) warehouse = row.rejected_warehouse;
						else if (field.fieldname.startsWith("from_")) warehouse = row.from_warehouse;
						else if (field.fieldname.startsWith("to_")) warehouse = row.t_warehouse || row.target_warehouse;
						else if (doctype === "Stock Entry") warehouse = row.s_warehouse;
						return { filters: { warehouse: warehouse || "", disabled: 0 } };
					});
				}
			}
		},
	});
}
