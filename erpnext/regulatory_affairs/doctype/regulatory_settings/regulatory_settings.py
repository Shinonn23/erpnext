# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

DEFAULT_FDA_QUERY_DAYS = (7, 3, 1, 0)


def parse_day_list(value: str | None) -> list[int]:
	"""Parse "7,3,1,0" into integers; raises ValueError on anything else."""
	return [int(day.strip()) for day in (value or "").split(",") if day.strip()]


class RegulatorySettings(Document):
	def validate(self):
		self._validate_rules()
		self._validate_numbers()
		self._validate_query_days()
		self._validate_recipients()

	def _validate_rules(self):
		days = []
		for rule in self.expiry_alert_rules:
			if rule.days_before_expiry is None or rule.days_before_expiry < 0:
				frappe.throw(_("Row {0}: Days Before Expiry cannot be negative.").format(rule.idx))
			days.append(rule.days_before_expiry)
		if len(days) != len(set(days)):
			frappe.throw(_("Each Days Before Expiry value can appear in only one alert rule."))
		if self.enable_expiry_alerts and not days:
			frappe.throw(_("Add at least one expiry alert rule or disable expiry alerts."))

	def _validate_numbers(self):
		if (self.overdue_repeat_days or 0) < 1:
			frappe.throw(_("The overdue reminder interval must be at least one day."))
		if self.auto_create_renewal_action and (self.renewal_start_days_before_expiry or 0) < 0:
			frappe.throw(_("Renewal Start Days Before Expiry cannot be negative."))

	def _validate_query_days(self):
		try:
			days = parse_day_list(self.fda_query_alert_days)
		except ValueError:
			frappe.throw(_("Enter FDA Query Alert Days as comma-separated numbers, e.g. 7,3,1,0."))
		if any(day < 0 for day in days) or len(days) != len(set(days)):
			frappe.throw(_("FDA Query Alert Days must be unique and cannot be negative."))
		if not days:
			self.fda_query_alert_days = ",".join(str(day) for day in DEFAULT_FDA_QUERY_DAYS)

	def _validate_recipients(self):
		seen = set()
		for row in self.get("recipients") or []:
			if row.user in seen:
				frappe.throw(
					_("Row {0}: {1} is already listed as an additional recipient.").format(
						row.idx, frappe.bold(row.user)
					)
				)
			seen.add(row.user)
