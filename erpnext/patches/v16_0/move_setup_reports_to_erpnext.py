"""Keep the site's report settings when these standard reports change modules."""

import frappe

REPORT_MODULES = {
	"Customer Demo Consumable Analysis": "Borrowing",
	"Repair Parts Ledger": "Support",
	"Selling Price vs Standard": "Selling",
	"Landed Cost Ledger": "Stock",
	"Stock Age Limits": "Stock",
	"Lost Stock": "Stock",
	"Stock Count Surplus": "Stock",
	"Monthly Shipments": "Stock",
}


def execute():
	for report, module in REPORT_MODULES.items():
		if frappe.db.exists(
			"Report", {"name": report, "module": "Connect Diagnostics Setup", "is_standard": "Yes"}
		):
			# Advance modified so model sync skips replacing local roles and report settings.
			frappe.db.set_value("Report", report, "module", module)
