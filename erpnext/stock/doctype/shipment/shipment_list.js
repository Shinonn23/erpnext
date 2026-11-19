// The Shipment Type (shipment_purpose) colours a submitted shipment in the list (Siwat, 18 Nov 2026: the list shows
// whether it is a sale or a Demo / Borrowing delivery); a Draft or Cancelled one keeps the standard indicator.
const SHIPMENT_PURPOSE_COLOURS = {
	"Sale Delivery": "blue",
	Borrowing: "purple",
	Demo: "pink",
	"Stock Transfer": "cyan",
	Purchase: "orange",
	Return: "yellow",
	Miscellaneous: "gray",
};

frappe.listview_settings["Shipment"] = {
	add_fields: ["status", "shipment_purpose"],
	get_indicator: function (doc) {
		if (doc.status == "Booked") {
			return [__("Booked"), "green"];
		}
		if (doc.shipment_purpose) {
			return [
				__(doc.shipment_purpose),
				SHIPMENT_PURPOSE_COLOURS[doc.shipment_purpose] || "gray",
				"shipment_purpose,=," + doc.shipment_purpose,
			];
		}
	},
};
