"""Names shared by the Regulatory Affairs (RA) module."""

MODULE = "Regulatory Affairs"

PRODUCT_REGISTRATION = "Regulatory Product Registration"
REGISTERED_PRODUCT = "Regulatory Registered Product"
ESTABLISHMENT_REGISTRATION = "Regulatory Establishment Registration"
REGULATORY_DOCUMENT = "Regulatory Document"
REGISTRATION_ACTION = "Regulatory Registration Action"
REGULATORY_OVERRIDE = "Regulatory Override"
ALERT_LOG = "Regulatory Alert Log"
SETTINGS = "Regulatory Settings"
ALERT_RULE = "Regulatory Expiry Alert Rule"
ALERT_RECIPIENT = "Regulatory Alert Recipient"
RENEWAL_HISTORY = "Regulatory Renewal History"

ROLE_VIEWER = "Regulatory Viewer"
ROLE_OPERATOR = "Regulatory Operator"
ROLE_APPROVER = "Regulatory Approver"
MANAGED_ROLES = (ROLE_VIEWER, ROLE_OPERATOR, ROLE_APPROVER)

# Transactions that carry a regulatory snapshot, mapped to their item child table.
TRANSACTION_ITEM_DOCTYPES = {
	"Purchase Order": "Purchase Order Item",
	"Purchase Receipt": "Purchase Receipt Item",
	"Stock Entry": "Stock Entry Detail",
	"Sales Order": "Sales Order Item",
	"Delivery Note": "Delivery Note Item",
	"Sales Invoice": "Sales Invoice Item",
}
TRANSACTION_DOCTYPES = tuple(TRANSACTION_ITEM_DOCTYPES)
OUTBOUND_DOCTYPES = frozenset({"Delivery Note", "Sales Invoice"})

# Registration statuses
DRAFT = "Draft"
ACTIVE = "Active"
SUSPENDED = "Suspended"
REVOKED = "Revoked"
CANCELLED = "Cancelled"
EXPIRED = "Expired"
SUPERSEDED = "Superseded"

# A registration in one of these statuses can be in force on a date (the dates decide).
IN_FORCE_STATUSES = (ACTIVE, EXPIRED, SUPERSEDED)

EXPIRY_SPECIFIED = "Expiry Date Specified"
NO_EXPIRY = "No Expiry Date Specified"
EXPIRY_UNCONFIRMED = "Unconfirmed"

# Alert kinds
ALERT_REGISTRATION = "Registration Expiry"
ALERT_ESTABLISHMENT = "Establishment Expiry"
ALERT_DOCUMENT = "Supporting Document Expiry"
ALERT_FDA_QUERY = "FDA Query Due"

# Open renewal stages end with one of these.
CLOSED_STAGES = ("Approved", "Rejected", "Cancelled")

# Product categories a registration or an Item can belong to (the Thai FDA product groups).
MEDICAL_DEVICE = "Medical Device"
PRODUCT_CATEGORIES = (
	MEDICAL_DEVICE,
	"Food",
	"Drug",
	"Cosmetic",
	"Hazardous Substance",
	"Herbal Product",
	"Other",
)
OTHER_CATEGORY = "Other"

# Item classification: the assessment status Custom Field that already exists on Item holds the
# decision whether the Item needs an FDA registration (it replaces the former "Is Medical Device" +
# "Registration Requirement" pair).
ASSESSMENT_FIELD = "custom_thai_fda_applicability"
ASSESSMENT_NOTE_FIELD = "custom_thai_fda_assessment_note"
ASSESSMENT_NOT_ASSESSED = "Not Assessed"
ASSESSMENT_REQUIRED = "Registration Required"
ASSESSMENT_EXEMPT = "Not Required / Exempt"
ASSESSMENT_STATUSES = (ASSESSMENT_NOT_ASSESSED, ASSESSMENT_REQUIRED, ASSESSMENT_EXEMPT)

ITEM_COVERAGE_FIELDS = (
	"current_regulatory_registration",
	"current_newcode",
	"current_registration_expiry",
	"current_registration_status",
)
