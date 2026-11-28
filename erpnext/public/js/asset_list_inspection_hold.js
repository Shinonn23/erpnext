// Native Asset return inspection.
// The Asset list marks an asset that waits for the inspection of its return with an orange "Awaiting Inspection"
// (Siwat, 9 Nov 2026; assets/inspection_hold.py). The status indicator of ERPNext is kept for every other asset.
(function () {
	const settings = frappe.listview_settings["Asset"] || {};
	const original = settings.get_indicator;
	frappe.listview_settings["Asset"] = Object.assign(settings, {
		add_fields: [...(settings.add_fields || []), "awaiting_inspection"],
		get_indicator(doc) {
			if (doc.awaiting_inspection) {
				return [__("Awaiting Inspection"), "orange", "awaiting_inspection,=,1"];
			}
			return original ? original(doc) : undefined;
		},
	});
})();
