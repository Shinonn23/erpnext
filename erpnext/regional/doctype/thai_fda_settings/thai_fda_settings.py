# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ThaiFDASettings(Document):
	def validate(self):
		try:
			days = [int(day.strip()) for day in (self.reminder_days or "").split(",") if day.strip()]
		except ValueError:
			frappe.throw(_("ระบุวันเตือนเป็นตัวเลขคั่นด้วยจุลภาค เช่น 90,60,30,7"))

		if not days or any(day < 0 for day in days):
			frappe.throw(_("ต้องมีวันเตือนอย่างน้อยหนึ่งค่า และห้ามเป็นจำนวนติดลบ"))
		if len(days) != len(set(days)):
			frappe.throw(_("วันเตือนต้องไม่ซ้ำกัน"))
		if int(self.overdue_repeat_days or 0) < 1:
			frappe.throw(_("ระยะเตือนซ้ำหลังหมดอายุต้องไม่น้อยกว่า 1 วัน"))

		users = [row.user for row in self.recipients if row.enabled and row.user]
		if len(users) != len(set(users)):
			frappe.throw(_("ผู้รับแจ้งเตือนในรายการกลางต้องไม่ซ้ำกัน"))
