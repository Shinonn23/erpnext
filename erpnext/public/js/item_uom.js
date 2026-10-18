// Item UOM pickers list only the UOMs the Item converts: its stock UOM and its UOMs table (and its template's).
// The pairing mirrors get_item_uom_fields in erpnext/stock/item_uom.py, which also rejects any other UOM on save.
frappe.provide("erpnext.item_uom");

// `uom` belongs to the row's Item; `<prefix>_uom` to `<prefix>` or `<prefix>_code` (free_item_uom → free_item).
erpnext.item_uom.item_field = (fieldname, fields) => {
	const prefix = fieldname.endsWith("_uom") ? fieldname.slice(0, -"_uom".length) : null;
	const candidates =
		fieldname === "uom"
			? ["item_code", "item", "production_item"]
			: prefix
			? [prefix, `${prefix}_code`]
			: [];
	return candidates.find((candidate) =>
		(fields || []).some(
			(df) => df.fieldname === candidate && df.fieldtype === "Link" && df.options === "Item"
		)
	);
};

{
	const ControlLink = frappe.ui.form.ControlLink;
	if (ControlLink && !ControlLink.prototype.__item_uom_query) {
		const set_custom_query = ControlLink.prototype.set_custom_query;
		ControlLink.prototype.set_custom_query = function (args) {
			set_custom_query.call(this, args);
			// A form's own server query keeps its rules (Pricing Rule, POS, transaction items).
			// Advanced Search builds its args without the doctype.
			const doctype = args.doctype || this.get_options?.();
			if (doctype !== "UOM" || args.query || !frappe.boot.sysdefaults?.restrict_item_uoms) return;

			// Grid rows (form or dialog), then the form or dialog itself.
			const fields =
				this.grid?.docfields ||
				this.layout?.fields ||
				(this.doctype ? frappe.meta.get_docfields(this.doctype) : []);
			const item_field = erpnext.item_uom.item_field(this.df.fieldname, fields);
			if (!item_field) return;

			const doc =
				this.doc || (this.doctype && this.docname ? locals[this.doctype]?.[this.docname] : null);
			const item_code = doc ? doc[item_field] : this.layout?.fields_dict?.[item_field]?.get_value?.();
			// A "same item" Pricing Rule or Promotional Scheme gives away the rule's own Items, so free_item is empty.
			if (item_field === "free_item" && !item_code && doc?.same_item) return;
			// No Item yet means no UOM to offer.
			args.query = "erpnext.controllers.queries.get_item_uom_query";
			args.filters = { item_code: item_code || "" };
		};
		ControlLink.prototype.__item_uom_query = true;
	}
}
