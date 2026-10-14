// Item preferences and compatible locations share one permission-aware server query.
frappe.provide("erpnext.queries");
erpnext.queries.storage_location = (warehouse, item_code) => ({
	query: "erpnext.stock.doctype.storage_location.storage_location.storage_location_query",
	filters: { warehouse: warehouse || "", item_code: item_code || "" },
});

for (const doctype of [
	"BOM",
	"Delivery Note",
	"Job Card",
	"Material Request",
	"Item",
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
				if (
					fieldname === "storage_location" &&
					row.rm_item_code &&
					row.parentfield === "supplied_items"
				) {
					return frm.doc.supplier_warehouse;
				}
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
					const result = erpnext.queries.storage_location(
						warehouse_for_field(fieldname, row),
						row.item_code ||
							row.rm_item_code ||
							([
								"fg_storage_location",
								"target_storage_location",
								"to_storage_location",
								"default_target_storage_location",
								"warehouse_storage_location",
							].includes(fieldname)
								? row.finished_good || row.production_item || row.item
								: "")
					);
					if (doctype === "Item") {
						// Preview the unsaved condition while editing Item preferences.
						result.filters.item_code = "";
						result.filters.storage_condition = doc.storage_condition || "";
					}
					return result;
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
			if (doctype === "Item") {
				frm.set_query("warehouse", "preferred_storage_locations", () => ({
					filters: { is_group: 0, disabled: 0 },
				}));
			}
		},
		refresh(frm) {
			if (frm.meta.fields.some((field) => field.fieldname === "apply_putaway_rule")) {
				const enabled = frappe.boot.sysdefaults.enable_putaway_rule !== 0;
				if (!enabled && !frm.doc.docstatus && frm.doc.apply_putaway_rule) {
					frm.set_value("apply_putaway_rule", 0);
				}
				frm.toggle_display("apply_putaway_rule", enabled);
			}
		},
	});
}
