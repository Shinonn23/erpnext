// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.ui.form.on("Warehouse", {
	setup: function (frm) {
		frm.set_query("default_storage_location", () => ({
			filters: { warehouse: frm.doc.name, disabled: 0 },
		}));
		frm.set_query("default_in_transit_warehouse", function (doc) {
			return {
				filters: {
					warehouse_type: "Transit",
					is_group: 0,
					company: doc.company,
				},
			};
		});

		frm.set_query("parent_warehouse", function (doc) {
			return {
				filters: {
					is_group: 1,
					company: doc.company,
				},
			};
		});

		frm.set_query("account", function (doc) {
			return {
				filters: {
					is_group: 0,
					account_type: "Stock",
					company: doc.company,
				},
			};
		});
	},

	warehouse_type: function (frm) {
		show_warehouse_type_summary(frm);
	},

	company: function (frm) {
		if (frm.doc.account) {
			frm.set_value("account", "");
		}
	},

	refresh: function (frm) {
		frm.toggle_display("warehouse_name", frm.doc.__islocal);
		frm.toggle_display(["address_html", "contact_html"], !frm.doc.__islocal);

		if (!frm.is_new()) {
			frappe.contacts.render_address_and_contact(frm);

			if (frm.has_perm("write")) {
				let enable_toggle = frm.doc.disabled ? "Enable" : "Disable";
				frm.add_custom_button(__(enable_toggle), () => {
					frm.set_value("disabled", 1 - frm.doc.disabled);
					frm.save();
				});

				frm.add_custom_button(
					frm.doc.is_group
						? __("Convert to Ledger", null, "Warehouse")
						: __("Convert to Group", null, "Warehouse"),
					function () {
						convert_to_group_or_ledger(frm);
					}
				);
			}

			if ("Stock Balance" in frappe.boot.allowed_reports) {
				frm.add_custom_button(
					__("Stock Balance"),
					function () {
						frappe.set_route("query-report", "Stock Balance", {
							warehouse: frm.doc.name,
							company: frm.doc.company,
						});
					},
					__("View")
				);
			}
		} else {
			frappe.contacts.clear_address_and_contact(frm);
		}

		if (
			!frm.doc.is_group &&
			frm.doc.__onload?.account &&
			"General Ledger" in frappe.boot.allowed_reports
		) {
			frm.add_custom_button(
				__("General Ledger", null, "Warehouse"),
				function () {
					frappe.route_options = {
						account: frm.doc.__onload.account,
						company: frm.doc.company,
					};
					frappe.set_route("query-report", "General Ledger");
				},
				__("View")
			);
		}

		frm.toggle_enable(["is_group", "company"], false);
		show_warehouse_type_summary(frm);

		if (frm.doc.customer) {
			frm.set_df_property("customer", "read_only", frm.doc.__onload.stock_exists);
		}
	},
});

function convert_to_group_or_ledger(frm) {
	frappe.call({
		method: "erpnext.stock.doctype.warehouse.warehouse.convert_to_group_or_ledger",
		args: {
			docname: frm.doc.name,
			is_group: frm.doc.is_group,
		},
		callback: function () {
			frm.refresh();
		},
	});
}

const OPERATION_LABELS = {
	receipt: "Receipt",
	issue: "Issue",
	transfer: "Transfer",
	sales: "Sales",
	loan: "Loan",
	reconciliation: "Reconciliation",
};

function render_warehouse_type_summary(summary) {
	if (!summary || !summary.restricted) {
		return __("No Warehouse Type: no restriction");
	}
	const allowed = summary.operations.filter((row) => row.allowed);
	const blocked = summary.operations.filter((row) => !row.allowed);
	const name = (row) => __(OPERATION_LABELS[row.operation] || row.operation);
	const parts = [
		allowed.length ? __("Allowed: {0}", [allowed.map(name).join(", ")]) : __("Allowed: nothing"),
	];
	if (blocked.length) {
		parts.push(__("Not allowed: {0}", [blocked.map(name).join(", ")]));
	}
	if (summary.outgoing_role) {
		parts.push(
			__("Moving stock out requires the {0} role", [frappe.utils.escape_html(summary.outgoing_role)])
		);
	}
	if (summary.loan_counts_as_transfer) {
		parts.push(__("Loan documents count as Transfer"));
	}
	return parts.join(". ");
}

function render_warehouse_type_detail(summary) {
	if (!summary || !summary.restricted) {
		return "";
	}
	const esc = frappe.utils.escape_html;
	const rows = summary.operations
		.map(
			(row) =>
				`<li>${row.allowed ? "&#10003;" : "&#10007;"} <b>${esc(
					__(OPERATION_LABELS[row.operation] || row.operation)
				)}</b>: ${esc(row.documents.join("; "))}</li>`
		)
		.join("");
	return `<ul class="list-unstyled small">${rows}</ul>`;
}

function show_warehouse_type_summary(frm) {
	const warehouse_type = frm.doc.is_group ? null : frm.doc.warehouse_type;
	const apply = (summary) => {
		if (warehouse_type !== (frm.doc.is_group ? null : frm.doc.warehouse_type)) {
			return; // the type changed while the request was running
		}
		const line = render_warehouse_type_summary(summary);
		frm.set_intro("");
		if (!frm.doc.is_group) {
			frm.set_intro(line + render_warehouse_type_detail(summary), "blue");
			frm.set_df_property("warehouse_type", "description", line);
		}
	};
	if (!warehouse_type) {
		apply(null);
		return;
	}
	frappe.call({
		method: "erpnext.stock.warehouse_policy.get_warehouse_type_summary",
		args: { warehouse_type },
		callback: (r) => apply(r.message),
	});
}
