"""Thailand regional definitions of ERPNext.

The Thailand fields of every DocType of ERPNext and HRMS are in the DocType JSON of the fork (Siwat, 8 Nov 2026; the
installer no longer makes them as Custom Fields). Only the fields of Frappe core DocTypes stay here, because a fork
cannot change a core DocType: Address, Print Format and Currency (9 fields). `erpnext.regional.thailand.install`
creates them.
"""

ERP_CUSTOM_FIELDS = {
	"Address": [
		{
			"fieldname": "tax_id",
			"fieldtype": "Data",
			"label": "Tax ID",
			"insert_after": "disabled",
		},
		{
			"fieldname": "branch_code",
			"fieldtype": "Data",
			"label": "Branch Code",
			"insert_after": "tax_id",
		},
		{
			"fieldname": "update_tax_branch",
			"fieldtype": "Check",
			"label": "Update Tax ID/Branch Code",
			"insert_after": "branch_code",
			"description": "Update Tax ID and Brach Code to linked Customer/Supplier",
		},
	],
	"Print Format": [
		{
			"fieldname": "default_condition",
			"fieldtype": "Data",
			"label": "Default Condition",
			"description": "Use python expression to set default format, i.e., doc.doctype=='Sales Invoice'",
			"insert_after": "disabled",
		},
		{
			"fieldname": "hide_if_not_default",
			"fieldtype": "Check",
			"label": "Hide if not Default",
			"description": "If default condition is not met, hide this format form selection",
			"insert_after": "default_condition",
		},
		{
			"fieldname": "add_comment_info",
			"fieldtype": "Check",
			"label": "Add Comment",
			"description": "Add printed as comment in document activity",
			"insert_after": "hide_if_not_default",
		},
		{
			"fieldname": "add_copies",
			"fieldtype": "Select",
			"label": "Additional Copies",
			"options": "\n1\n2\n3\n4",
			"description": "2 additional copies will print 3 copies, 1 original and 2 copies. For jinja template, doc.copy = 0 is original, else copies.",
			"insert_after": "add_comment_info",
		},
	],
	"Currency": [
		{
			"fieldname": "bot_currency",
			"fieldtype": "Data",
			"insert_after": "fraction_value",
			"label": "BOT Currency",
		},
		{
			"fieldname": "bot_currency_rate_type",
			"fieldtype": "Select",
			"insert_after": "bot_currency",
			"label": "BOT Currency Rate Type",
			"options": "\nMid Rate\nSelling Rate\nBuying Sight Rate\nBuying Transfer Rate",
			"default": "Selling Rate",
		},
	],
}

ERP_PROPERTY_SETTERS = {
	# per company: the account for tax we withhold (Pay) next to the one for tax withheld from us (Receive)
	"Tax Withholding Account": [
		("account", "label", "Pay Account (tax we withhold)", "Data"),
		("account", "reqd", "0", "Check"),
		("account", "columns", "4", "Int"),
		(
			"account",
			"description",
			"Liability account for the tax we withhold from suppliers (PND3 / PND53). Required when Applies To "
			"includes Pay.",
			"Small Text",
		),
		("company", "columns", "2", "Int"),
	],
	"Purchase Taxes and Charges": [
		("rate", "precision", "6", "Select"),
	],
	"Sales Taxes and Charges": [
		("rate", "precision", "6", "Select"),
	],
	"Advance Taxes and Charges": [
		("rate", "precision", "6", "Select"),
	],
	"Sales Invoice": [
		("naming_series", "depends_on", "eval:!doc.amended_from", "Data"),
	],
	# named ST/PT + BE year + month in code (custom/tax_invoice_series.py), the series is not used
	# (reqd first: every Property Setter validates the DocType at once and refuses hidden + reqd without a default)
	("Sales Tax Invoice", "Purchase Tax Invoice"): [
		("naming_series", "reqd", "0", "Check"),
		("naming_series", "hidden", "1", "Check"),
	],
	("Quotation", "Sales Order", "Delivery Note", "Sales Invoice"): [
		("company_address", "label", "Company Branch (Company Address)", "Data"),
	],
	"Purchase Invoice": [
		("naming_series", "depends_on", "eval:!doc.amended_from", "Data"),
	],
	"Document Naming Settings": [
		(
			"help_html",
			"options",
			'<div class="well">\n    Edit list of Series in the box. Rules:\n    <ul>\n        <li>Each Series Prefix on a new line.</li>\n        <li>Allowed special characters are "/" and "-"</li>\n        <li>\n            Optionally, set the number of digits in the series using dot (.)\n            followed by hashes (#). For example, ".####" means that the series\n            will have four digits. Default is five digits.\n        </li>\n        <li>\n            You can also use variables in the series name by putting them\n            between (.) dots\n            <br>\n            Supported Variables:\n            <ul>\n                <li><code>.YYYY.</code> - Year in 4 digits</li>\n                <li><code>.YYYY-DATE.</code> - Year in 4 digits by Document Date</li>\n                <li><code>.YY.</code> - Year in 2 digits</li>\n                <li><code>.YY-DATE.</code> - Year in 2 digits by Document Date</li>\n                <li><code>.MM.</code> - Month</li>\n                <li><code>.MM-DATE.</code> - Month by Document Date</li>\n                <li><code>.DD.</code> - Day of month</li>\n                <li><code>.DD-DATE.</code> - Day of month by Document Date</li>\n                <li><code>.WW.</code> - Week of the year</li>\n                <li><code>.WW-DATE.</code> - Week of the year by Document Date</li>\n                <li><code>.FY.</code> - Fiscal Year</li>\n                <li>\n                    <code>.{fieldname}.</code> - fieldname on the document e.g.\n                    <code>branch</code>\n                </li>\n            </ul>\n        </li>\n    </ul>\n    Examples:\n    <ul>\n        <li>INV-</li>\n        <li>INV-10-</li>\n        <li>INVK-</li>\n        <li>INV-.YYYY.-.{branch}.-.MM.-.####</li>\n    </ul>\n</div>\n<br>\n',
			"Text",
		),
	],
	"Currency Exchange Settings": [
		(
			"service_provider",
			"options",
			"frankfurter.dev\nexchangerate.host\nBank of Thailand\nCustom",
			"Select",
		),
	],
}
