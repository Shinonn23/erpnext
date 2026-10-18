import frappe

RENEWAL_STATUSES = {
	"ยังไม่เริ่ม": "Not Started",
	"กำลังดำเนินการ": "In Progress",
	"ยื่นแล้ว": "Submitted",
	"ได้รับฉบับใหม่": "New Document Received",
}

SELECT_TRANSLATIONS = {
	"Item": {
		"custom_thai_fda_applicability": {
			"ยังไม่ประเมิน": "Not Assessed",
			"ต้องมีทะเบียน": "Registration Required",
			"ไม่ต้องมี/ได้รับยกเว้น": "Not Required / Exempt",
		},
	},
	"Thai FDA Registration": {
		"regulatory_category": {
			"อาหาร": "Food",
			"ยา": "Drug",
			"เครื่องสำอาง": "Cosmetic",
			"เครื่องมือแพทย์": "Medical Device",
			"วัตถุอันตราย": "Hazardous Substance",
			"ผลิตภัณฑ์สมุนไพร": "Herbal Product",
			"อื่นๆ": "Other",
		},
		"status": {
			"รออนุมัติ": "Pending Approval",
			"มีผล": "Active",
			"พักใช้": "Suspended",
			"เพิกถอน": "Revoked",
			"ยกเลิก": "Cancelled",
			"ถูกแทนที่": "Superseded",
		},
		"activity": {
			"ผลิต": "Manufacture",
			"นำเข้า": "Import",
			"ขาย": "Sale",
			"ขึ้นทะเบียนผลิตภัณฑ์": "Product Registration",
			"อื่นๆ": "Other",
		},
		"expiry_basis": {
			"มีวันหมดอายุตามเอกสาร": "Expiry Date Specified",
			"เอกสารไม่ระบุวันหมดอายุ": "No Expiry Date Specified",
			"ยังยืนยันไม่ได้": "Unconfirmed",
		},
		"renewal_status": RENEWAL_STATUSES,
	},
	"Thai FDA Renewal Log": {
		"stage": {**RENEWAL_STATUSES, "ไม่อนุมัติ/ยุติ": "Rejected / Discontinued"},
	},
	"Thai FDA Reminder Log": {
		"status": {
			"กำลังส่ง": "Sending",
			"ส่งแล้ว": "Sent",
			"รอส่งซ้ำ": "Pending Retry",
			"ยกเลิก": "Cancelled",
		},
	},
}


def execute():
	"""Translate Thai select values in place.

	The legacy Thai FDA DocTypes are retired (their tables are only read by the merge patch), so this only
	updates rows of tables that still exist; the Item field definition is owned by Regulatory Affairs.
	"""
	for doctype, fields in SELECT_TRANSLATIONS.items():
		if not frappe.db.table_exists(doctype):
			continue
		for fieldname, translations in fields.items():
			if not frappe.db.has_column(doctype, fieldname):
				continue
			for old_value, new_value in translations.items():
				# Plain SQL: the DocType records of the retired legacy tables may already be gone.
				frappe.db.sql(
					f"update `tab{doctype}` set `{fieldname}` = %s where `{fieldname}` = %s",
					(new_value, old_value),
				)
