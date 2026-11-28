// A submitted Purchase Receipt that rejected goods (rejected in quarantine, or received into its Rejected Warehouse):
// purchasing sends them back to the supplier with one button under Create, the Purchase Return of exactly those goods
// (Siwat, 5 Nov 2026; the task "Return rejected goods" of my_space/inspections.py makes the same document).
const REJECTED_API = "erpnext.stock.quarantine.quarantine_inspection.";

frappe.ui.form.on("Purchase Receipt", {
	async refresh(frm) {
		if (frm.doc.docstatus !== 1 || frm.doc.is_return) return;
		if (!frappe.user.has_role("Purchase User")) return;
		const { message } = await frappe.call({
			method: REJECTED_API + "rejected_goods_to_return",
			args: { name: frm.doc.name },
		});
		if (!(flt(message) > 0)) return;
		frm.add_custom_button(
			__("Return rejected goods"),
			async () => {
				const { message: made } = await frappe.call({
					method: REJECTED_API + "make_return_rejected",
					args: { name: frm.doc.name },
					freeze: true,
				});
				if (made) {
					frappe.model.sync(made);
					frappe.set_route("Form", made.doctype, made.name);
				}
			},
			__("Create"),
			// The return is a Purchase Receipt; the Create filter (downstream_action_permissions.js) reads this target,
			// not the label, which is no DocType.
			"Purchase Receipt"
		);
	},
});
