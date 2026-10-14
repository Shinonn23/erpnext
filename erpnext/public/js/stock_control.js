// Add stock policy predicates without replacing native availability or permission queries.
frappe.provide("erpnext.stock_control");

erpnext.stock_control.add_filters = function (query, doctype, fieldname, values) {
	const result = typeof query === "string" ? { query } : { ...(query || {}) };
	const predicate = ["in", values.length ? values : [""]];
	if (Array.isArray(result.filters)) {
		result.filters = [...result.filters, [doctype, fieldname, ...predicate]];
	} else {
		// Preserve an existing location constraint as an AND predicate.
		const filters = result.filters || {};
		if (Object.hasOwn(filters, fieldname)) {
			result.filters = Object.entries(filters).map(([key, value]) => [
				doctype, key, ...(Array.isArray(value) ? value : ["=", value]),
			]);
			result.filters.push([doctype, fieldname, ...predicate]);
		} else {
			result.filters = { ...filters, [fieldname]: predicate };
		}
	}
	return result;
};

const stock_control_doctypes = [
	"Quotation", "Sales Order", "Delivery Note", "Sales Invoice", "POS Invoice",
	"Material Request", "Stock Entry", "Pick List", "Asset Movement", "Custody Loan",
];

for (const doctype of stock_control_doctypes) {
	frappe.ui.form.on(doctype, {
		setup(frm) {
			const native_set_query = frm.set_query;
			frm.set_query = function (...args) {
				const result = native_set_query.apply(this, args);
				erpnext.stock_control.wrap_queries(frm);
				return result;
			};
			erpnext.stock_control.wrap_queries(frm);
		},
		onload: refresh_stock_control_options,
		refresh: refresh_stock_control_options,
		onload_post_render(frm) { erpnext.stock_control.wrap_queries(frm); },
		company: refresh_stock_control_options,
		purpose: refresh_stock_control_options,
		is_return: refresh_stock_control_options,
		material_request_type: refresh_stock_control_options,
		material_request: refresh_stock_control_options,
		custody_loan: refresh_stock_control_options,
		reference_doctype: refresh_stock_control_options,
		reference_name: refresh_stock_control_options,
	});
}

async function refresh_stock_control_options(frm) {
	const request = (frm.__stock_control_request || 0) + 1;
	frm.__stock_control_request = request;
	// Fail closed while a new company or activity is being checked.
	frm.__stock_control_options = { operation: "pending", warehouse_types: [], locations: [] };
	erpnext.stock_control.wrap_queries(frm);
	const response = await frappe.call({
		method: "erpnext.controllers.queries.get_stock_control_source_options",
		args: { doc: frm.doc },
	});
	if (frm.__stock_control_request === request) {
		frm.__stock_control_options = response.message;
		erpnext.stock_control.wrap_queries(frm);
	}
}

erpnext.stock_control.wrap_queries = function (frm) {
	const wrap = (field, link_doctype, fieldname, source_fieldname) => {
		if (!field || field.get_query?.__stock_control_wrapper) return;
		const native_query = field.get_query || field.df?.get_query;
		const wrapper = function (...args) {
			let query = typeof native_query === "function"
				? native_query.apply(this, args) : native_query;
			if (native_query && typeof native_query === "object" && !query.filters && !query.query) {
				query = { filters: native_query };
			}
			const policy = frm.__stock_control_options;
			if (!policy?.operation) return query;
			// Material Request's warehouse is the destination except for Material Issue.
			if (frm.doc?.doctype === "Material Request" &&
				["warehouse", "set_warehouse"].includes(source_fieldname) &&
				frm.doc.material_request_type !== "Material Issue") return query;
			// This custom query applies the same policy on the server and has its own filter shape.
			if (query?.query === "erpnext.assets.doctype.material_request.material_request.asset_query") {
				return query;
			}
			return erpnext.stock_control.add_filters(
				query, link_doctype, fieldname,
				link_doctype === "Warehouse" ? policy.warehouse_types : policy.locations
			);
		};
		wrapper.__stock_control_wrapper = true;
		field.get_query = wrapper;
	};
	const source_warehouse_fields = new Set([
		"warehouse", "set_warehouse", "from_warehouse", "set_from_warehouse",
		"s_warehouse", "source_warehouse",
	]);
	const visit = (fields, get_field) => {
		for (const df of fields || []) {
			if (df.fieldtype !== "Link") continue;
			if (df.options === "Warehouse" && source_warehouse_fields.has(df.fieldname)) {
				wrap(get_field(df.fieldname), "Warehouse", "warehouse_type", df.fieldname);
			} else if (df.options === "Asset" && df.fieldname === "asset") {
				wrap(get_field(df.fieldname), "Asset", "location");
			}
		}
	};
	visit(frm.meta.fields, (name) => frm.fields_dict[name]);
	for (const df of frm.meta.fields || []) {
		if (df.fieldtype !== "Table") continue;
		const grid = frm.fields_dict[df.fieldname]?.grid;
		if (grid) visit(frappe.get_meta(df.options)?.fields, (name) => grid.get_field(name));
	}
};

// Row-level request references can change the activity of a Stock Entry or Pick List.
for (const doctype of ["Stock Entry Detail", "Pick List Item"]) {
	frappe.ui.form.on(doctype, {
		material_request: refresh_stock_control_options,
		material_request_item: refresh_stock_control_options,
	});
}
