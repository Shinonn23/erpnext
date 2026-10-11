// Warehouse-specific queries for the built-in Storage Location inventory dimension.
for (const doctype of [
	"BOM",
	"Delivery Note",
	"Job Card",
	"Material Request",
	"Plant Floor",
	"Production Plan",
	"Purchase Invoice",
	"Purchase Receipt",
	"Sales Invoice",
	"Stock Entry",
	"Stock Reconciliation",
	"Stock Reservation Entry",
	"Subcontracting Receipt",
	"Work Order",
]) {
	frappe.ui.form.on(doctype, {
		setup(frm) {
			const warehouse_for_field = (fieldname, row) => {
				if (fieldname.startsWith("rejected_")) return row.rejected_warehouse;
				if (fieldname.startsWith("from_")) return row.from_warehouse;
				if (fieldname.startsWith("to_")) {
					return row.to_warehouse || row.t_warehouse || row.target_warehouse;
				}

				if (doctype === "Stock Entry") return row.s_warehouse;

				const warehouse_field = {
					default_source_storage_location: "default_source_warehouse",
					default_target_storage_location: "default_target_warehouse",
					warehouse_storage_location: "warehouse",
					for_storage_location: "for_warehouse",
					sub_assembly_storage_location: "sub_assembly_warehouse",
					source_storage_location: "source_warehouse",
					wip_storage_location: "wip_warehouse",
					fg_storage_location: "fg_warehouse",
					target_storage_location: "target_warehouse",
					scrap_storage_location: "scrap_warehouse",
					storage_location: "warehouse",
				}[fieldname];

				return warehouse_field ? row[warehouse_field] : null;
			};

			const set_storage_location_query = (fieldname, get_row) => {
				const query = (doc, cdt, cdn) => {
					const row = get_row ? locals[cdt][cdn] : doc;
					return {
						filters: {
							warehouse: warehouse_for_field(fieldname, row) || "",
							disabled: 0,
						},
					};
				};

				if (get_row) frm.set_query(fieldname, get_row.table, query);
				else frm.set_query(fieldname, query);
			};

			for (const field of (frm.meta.fields || []).filter(
				(df) => df.fieldtype === "Link" && df.options === "Storage Location"
			)) {
				set_storage_location_query(field.fieldname);
			}

			for (const table of frm.meta.fields.filter((df) => df.fieldtype === "Table")) {
				const meta = frappe.get_meta(table.options);
				for (const field of (meta?.fields || []).filter(
					(df) => df.fieldtype === "Link" && df.options === "Storage Location"
				)) {
					set_storage_location_query(field.fieldname, { table: table.fieldname });
				}
			}
		},
	});
}
