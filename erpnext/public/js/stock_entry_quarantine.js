// Goods put into quarantine (a submitted Stock Entry on the route "Quarantine Intake"), Siwat 5 Nov 2026, 9 Nov 2026: the QI
// team makes the Quality Inspections itself from the submitted intake; once they are submitted the Stock team takes the goods
// out with a Stock Entry of its own (button "Create Stock Entry" opens it prefilled), and the Stock Manager disposes of what
// went to DAMAGE (workflows/quarantine_inspection.py). A receipt is the same (public/js/receipt_quarantine.js).
const QUARANTINE_API = "erpnext.stock.quarantine.quarantine_inspection.";

// A move into or out of quarantine has no transit, shipment or additional cost (Siwat, 6 Nov 2026, UX review)
const QUARANTINE_HIDDEN_FIELDS = [
	"add_to_transit",
	"requires_shipment",
	"additional_costs_section",
];

frappe.ui.form.on("Stock Entry", {
	async refresh(frm) {
		if (
			["Quarantine Intake", "Quarantine Release"].includes(frm.doc.operation_approval_route)
		) {
			for (const fieldname of QUARANTINE_HIDDEN_FIELDS) {
				frm.set_df_property(fieldname, "hidden", 1);
			}
		}
		// A put-away row whose Item has no preferred Storage Location outside the Receiving one has no target bin:
		// the team chooses it (17 Nov 2026), so the empty cells are named instead of left to be found on the save. A
		// row whose target is a Receiving location is named too: the submit refuses it (WP1, 18 Nov 2026).
		if (frm.doc.docstatus === 0 && frm.doc.purpose === "Material Transfer") {
			const missing = await rows_to_place(frm.doc.items || []);
			if (missing.length) {
				frm.dashboard.set_headline(
					__("Choose the target Storage Location of row {0}.", [
						missing.map((row) => row.idx).join(", "),
					]),
					"orange",
					true
				);
			}
		}
		if (frm.doc.docstatus !== 1) return;
		const stock_team =
			frappe.user.has_role("Storage Location Manager") ||
			frappe.user.has_role("Stock Manager");
		if (frm.doc.purpose === "Material Transfer" && frappe.user.has_role("Stock Manager")) {
			// goods moved into DAMAGE: the Stock Manager drafts their disposal (one button, only while some are still there)
			const { message } = await frappe.call({
				method: QUARANTINE_API + "damage_status",
				args: { stock_entry: frm.doc.name },
			});
			if (message && message.remaining) {
				frm.add_custom_button(
					__("Dispose of the goods"),
					() => dispose(frm),
					__("Create"),
					"Stock Entry"
				);
			}
		}
		if (frm.doc.operation_approval_route !== "Quarantine Intake") return;
		// the app's Create > Quality Inspection replaces ERPNext's "Quality Inspection(s)" on an intake
		frm.remove_custom_button(__("Quality Inspection(s)"), __("Create"));
		if (frappe.user.has_role("Quality Inspection User")) {
			frm.add_custom_button(
				__("Quality Inspection"),
				() => open_inspections(frm),
				__("Create"),
				"Quality Inspection"
			);
		}
		if (!stock_team) return;
		// shown only while inspected goods of this intake are still in quarantine, never to say "nothing left"
		const { message } = await frappe.call({
			method: QUARANTINE_API + "release_status",
			args: { doctype: frm.doc.doctype, name: frm.doc.name },
		});
		if (message && message.ready) {
			frm.add_custom_button(
				__("Stock Entry"),
				() => create_release_entry(frm),
				__("Create"),
				"Stock Entry"
			);
		}
	},
});

// the rows whose target Storage Location must still be chosen: a move out of quarantine or a put-away with none, and
// any transfer row whose target is a Receiving location
async function rows_to_place(items) {
	const targets = [...new Set(items.map((row) => row.to_storage_location).filter(Boolean))];
	let receiving = new Set();
	if (targets.length) {
		const { message } = await frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Storage Location",
				filters: { name: ["in", targets], location_type: "Receiving", disabled: 0 },
				fields: ["name"],
				limit_page_length: 0,
			},
		});
		receiving = new Set(
			(Array.isArray(message) ? message : []).map((location) => location.name)
		);
	}
	return items.filter(
		(row) =>
			(row.s_warehouse && receiving.has(row.to_storage_location)) ||
			((row.against_stock_entry || row.ste_detail) && !row.to_storage_location)
	);
}

async function create_release_entry(frm) {
	const { message } = await frappe.call({
		method: QUARANTINE_API + "make_release_entry",
		args: { doctype: frm.doc.doctype, name: frm.doc.name },
		freeze: true,
	});
	if (message) {
		frappe.model.sync(message);
		frappe.set_route("Form", message.doctype, message.name);
	}
}

async function dispose(frm) {
	const { message } = await frappe.call({
		method: QUARANTINE_API + "make_damage_disposal",
		args: { stock_entry: frm.doc.name },
		freeze: true,
	});
	if (message) {
		frappe.model.sync(message);
		frappe.set_route("Form", message.doctype, message.name);
	}
}

async function open_inspections(frm) {
	const { message } = await frappe.call({
		method: QUARANTINE_API + "make_receipt_inspections",
		args: { doctype: frm.doc.doctype, name: frm.doc.name },
		freeze: true,
	});
	const names = (message && message.inspections) || [];
	const skipped = (message && message.skipped) || [];
	if (skipped.length) {
		frappe.msgprint({
			title: __("Not your Quality Inspection team"),
			message: skipped.map((line) => frappe.utils.escape_html(line)).join("<br>"),
			indicator: "orange",
		});
	}
	if (names.length === 1) {
		frappe.set_route("Form", "Quality Inspection", names[0]);
	} else if (names.length) {
		frappe.route_options = { name: ["in", names] };
		frappe.set_route("List", "Quality Inspection");
	}
}
