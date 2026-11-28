// Native Asset return inspection.
// A returned asset waiting for the Quality Inspection of its return (Siwat, 9 Nov 2026; assets/inspection_hold.py): an orange
// indicator on the form and a line that names the return. The Inspection section itself shows only while the hold lasts.
frappe.ui.form.on("Asset", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.awaiting_inspection) return;
		frm.page.set_indicator(__("Awaiting Inspection"), "orange");
		if (frm.doc.inspection_hold_reference) {
			frm.dashboard.set_headline(
				__(
					"Waiting for the inspection of its return {0}. It cannot be lent until the Quality Inspection team has inspected it.",
					[
						frappe.utils.get_form_link(
							"Asset Movement",
							frm.doc.inspection_hold_reference,
							true
						),
					]
				),
				"orange"
			);
		}
	},
});

frappe.ui.form.on("Asset", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.docstatus !== 1 || !frappe.model.can_create("Material Request")) return;
		frm.add_custom_button(__("Material Request"), async () => {
			const { message: asset } = await frappe.call({
				method: "erpnext.assets.doctype.material_request.material_request.get_asset_request_details",
				args: {
					asset_name: frm.doc.name,
					company: frm.doc.company,
					material_request_type: "Asset Transfer",
				},
			});
			if (!asset) return;
			frappe.new_doc("Material Request", {
				company: asset.company,
				material_request_type: "Asset Transfer",
				asset_source_location: asset.location,
			}, (doc) => {
				const row = doc.items?.[0] || frappe.model.add_child(doc, "items");
				Object.assign(row, {
					asset: asset.name,
					asset_name: asset.asset_name,
					asset_serial_number: asset.asset_serial_number,
					item_code: asset.item_code,
					source_location: asset.location,
					item_type: "Fixed Asset",
					qty: 1,
					stock_qty: 1,
					uom: asset.stock_uom,
					stock_uom: asset.stock_uom,
					conversion_factor: 1,
				});
			});
		}, __("Create"));
	},
});
