# Regulatory Affairs (RA)

Regulatory master data for ERPNext covering **every Thai FDA product category** (Medical Device, Food, Drug, Cosmetic,
Hazardous Substance, Herbal Product, Other): product registrations and the Items they cover, establishment licences,
supporting evidence, renewal/variation history, expiry alerts, and policy-driven validation of purchase, stock and sales
documents with an immutable per-row snapshot.

RA is the single regulatory system of this app. It replaces the former Regional **Thai FDA Registration** feature (DocTypes
`Thai FDA Registration`, `Thai FDA Settings`, `Thai FDA Renewal Log`, `Thai FDA Reminder Log`, `Thai FDA Reminder Recipient`, the
*Thai FDA Compliance* report and its daily reminder job); the data patch described below moves the legacy records over.
RA started from the `connect_diagnostics_regulatory` module of `connect_diagnostics_core` and keeps its DocType names where they
did not change.

## Data model (module "Regulatory Affairs")

| DocType | Purpose |
|---|---|
| Regulatory Product Registration | One FDA registration (autoname = registration number). Child table **Registered Products / NEWCODE** maps NEWCODE/identifier lines to ERPNext Items with effective dates. |
| Regulatory Registered Product (child) | `newcode`, `product_name`, `identifier`, `item_code`, `physical_manufacturer`, `effective_from/to`, `line_status` (Active/Removed/Superseded). |
| Regulatory Establishment Registration | Company establishment licence (Import/Manufacture/Sale) referenced by product registrations. |
| Regulatory Document | Versioned evidence (Certificate, Annex, Label, IFU, LOA, ISO, GMP, ...). Own expiry; verification by a Regulatory Approver. |
| Regulatory Registration Action | Renewal / variation / new registration history with FDA application number, stage, FDA Query due date and outcome. Approving it updates the registration. |
| Regulatory Override | Maker-checker exception bound to one transaction name (and optionally one Item). |
| Regulatory Alert Log | One row per alert sent (idempotency key), with delivery status and retries. |
| Regulatory Settings (single) + Regulatory Expiry Alert Rule and Regulatory Alert Recipient (children) | Validation policy, renewal planning, alert thresholds/channels and extra alert recipients. |
| Regulatory Renewal History (child of a registration) | Read-only legacy renewal log rows carried over from Thai FDA Registration. |

Registration fields beyond the identity/lifecycle fields: `product_category` (required; Medical Device, Food, Drug, Cosmetic,
Hazardous Substance, Herbal Product, Other) with `other_category` (required for Other), `certificate_type` (License / Certificate
Type), `activity_type` (Import, Manufacture, Sale, Product Registration, Other), `issuing_authority` (default Thai FDA),
`holder_name`, `source_url`, `last_verified_on`, `responsible_user`, issue/expiry dates and `renewal_status`. The device-only fields
(`device_category`, `risk_class`, `grouping_type`, `device_description`, `intended_use`) show only for category Medical Device.
`legacy_thai_fda_records` (read-only, shown only when filled) lists the legacy records merged into the registration and
`legacy_renewal_history` (read-only table of **Regulatory Renewal History**) keeps the legacy renewal log.
**Regulatory Alert Recipient** (child of Settings) holds extra alert recipients.

### Item classification

An Item is **regulated** when its **Product Category** (`regulatory_product_category`, Regulatory tab, standard filter) is set.
Transactions are validated against a regulated Item's coverage, and only a regulated Item shows the *Current Regulatory Coverage*
section with the read-only coverage fields (`current_regulatory_registration`, `current_newcode`, `current_registration_expiry`,
`current_registration_status`), the red/orange headline alerts and the *Coverage Matrix* button. An Item without a Product Category
is not checked; saving it clears its coverage fields (saving a registration still stores coverage on every Item it maps, hidden on
unregulated ones). There is no separate assessment status, exemption reason or Not Assessed state: an Item that needs no FDA
registration simply has no Product Category.

The **Regulatory** tab also has `regulatory_other_category` (for Other), `medical_device_category` (only for Medical Device) and
`regulatory_owner`. The six guarded transactions get `regulatory_override_ref` (Link) and each item row gets a collapsed, read-only
**Regulatory Snapshot** section (`regulatory_*` fields listed in `custom_fields.SNAPSHOT_FIELDNAMES`). Custom Field definitions live
in `custom_fields.py` (RA is their only owner). The legacy Item fields *Thai FDA Assessment Status* (`custom_thai_fda_applicability`)
and *Thai FDA Assessment Reason / Notes* (`custom_thai_fda_assessment_note`) are retired by the patch described under
[Retiring the Thai FDA Assessment Status](#retiring-the-thai-fda-assessment-status).

## Roles

`Regulatory Viewer` (read/report/export), `Regulatory Operator` (+ create/write), `Regulatory Approver` (+ delete/share and the
approval powers below), plus `System Manager`. Roles are created by `install.ensure_roles` on install and migrate.
Users of the guarded documents (Stock, Sales, Purchase and Accounts roles) get *select* permission on Override only, so they can link an approved override on their document without being able to read or edit it.

Approver-only operations: changing a registration's official status, verifying/rejecting a document (any change to a verified document
voids the verification), approving an override (never by its requester; audit stamps cannot be edited), editing settings, changing the
issue date, expiry date or expiry basis of a registration that is no longer Draft. System, import, patch and install writes are not
restricted. Operators otherwise create and edit registrations, product lines, documents and actions. An Item's Product Category is
not guarded: anyone who may edit the Item may set or clear it.

## Registration lifecycle

`official_status` is read-only on the form and may change only through `set_official_status` (buttons on the form):

```
Draft -> Active
Active -> Suspended | Revoked | Cancelled | Superseded
Suspended -> Active | Revoked | Cancelled
Expired -> Active (after renewal) | Cancelled
Revoked, Cancelled, Superseded: terminal
```

`Active -> Expired` is automatic (daily job) once a stated expiry has passed. Activation requires at least one **Verified,
unexpired Certificate** document. `expiry_basis` is `Expiry Date Specified` (needs `expiry_date`), `No Expiry Date Specified`
(never expires) or `Unconfirmed` (treated as non-expiring but listed in Compliance Gaps; no expiry alerts).

Renewal paths when a **Registration Action** of type *Renewal* reaches stage *Approved*:
* **Same number**: `new_expiry_date` is applied to the registration (the previous expiry is stored on the action), an Expired
  registration is reactivated, and `renewal_status` becomes Approved.
* **New number**: `resulting_registration` is linked (`previous_registration` / `superseded_by`), the old registration becomes
  Superseded and its still-open product lines are closed the day before the successor's issue date.

Action stages sync `renewal_status` (Preparing, Internal Review, Submitted, FDA Query, Approved) unless the registration is set to
*Not Renewing*. *Not Renew* sets Not Renewing; an approved *Cancel* cancels the registration. `Expired` and `Superseded` are set by the
system only (expiry, or a successor taking over); the status buttons offer Activate, Reinstate, Suspend, Revoke and Cancel.

When a successor is activated with *Previous Registration* set, the predecessor's open product lines end the day before the successor's
issue date at that moment, so the two never cover an Item on the same day and the successor can be activated before the renewal action
is approved. Without *Previous Registration* an overlapping Active mapping is rejected.

## Coverage resolution (`coverage.py`)

An Item is covered on a date by its single **Active product line** whose registration is *in force* that day: status in
Active/Expired/Superseded, `issue_date <= date`, and `date <= expiry_date` when the basis is Expiry Date Specified. Renewals never
conflict because the predecessor and successor have different windows. More than one candidate is invalid master data and raises
("Ambiguous regulatory mapping"). If nothing is in force the registration that expired last is returned so the policy can say
*expired / suspended / revoked* rather than *missing*. `get_effective_mapping` additionally requires status Active.

## Transaction policy (`transactions.py`)

Hooks: PO and SO `validate` -> `validate_warning_transaction`; `before_submit` of PO, Purchase Receipt, Stock Entry, SO, Delivery
Note and Sales Invoice -> `capture_transaction_snapshot`; `before_submit` of Purchase Receipt, Stock Entry, Delivery Note and Sales
Invoice additionally -> `validate_transaction` (run first).

* Only rows whose Item has a Product Category are checked (any category). Saving a registration that maps an Item without one shows
  an orange alert asking to set it.
* A document type is skipped when its `validate_on_*` setting is off. **Returns** (`is_return`) are never validated (they still get a
  snapshot). **Stock Entry** is validated and snapshotted only for purpose *Material Receipt* so transfers, issues and
  write-offs of non-compliant stock stay possible. **Sales Invoice** rows linked to a Delivery Note (`dn_detail` or `delivery_note`)
  are not validated again; when `dn_detail` is set they inherit the Delivery Note row's snapshot.
* Coverage date: PO row `schedule_date` (then the document's, then `transaction_date`), SO row `delivery_date` (then the document's, then `transaction_date`), otherwise `posting_date`, falling back to `transaction_date`, then today.
* Consolidated Sales Invoices built by POS closing are not validated (POS Invoices themselves are not guarded). Purchase Order and
  Sales Order validation never raises: an unusable override or an ambiguous mapping is reported as a warning there.
* Per row checks (message prefix `Block:` or `Warning:` from the policy; `Ignore` drops it): no mapping effective on the date
  (`missing_registration_action`); mapping with neither a NEWCODE nor an Identifier (`missing_newcode_action`, labelled *Missing NEWCODE / Identifier Action*; migrated legacy lines carry only an Identifier, which is accepted); status Suspended / Revoked
  (`suspended_/revoked_registration_action`); any other non-covering status (Cancelled, Draft ...) counts as missing; expired
  (status Expired, or stated expiry before the coverage date) -> `expired_registration_action`, but for outbound documents the
  `expired_registration_existing_stock_policy` (Block / Warning / Allow Existing Compliant Stock = ignore); row `manufacturer`
  different from the mapped physical manufacturer (`manufacturer_mismatch_action`). An expiry is reported once.
* Outcome: any `Block:` message raises unless an approved override applies; only warnings -> orange message; warn-only entry points
  always message.
* **Override** (`regulatory_override_ref` on the document): must be Approved, bound to this `doctype` + `name`, and if the
  override has an `item_code`, only rows of that Item are exempted. The snapshot records `regulatory_override` on every in-scope row the override covers, including rows that have no mapping (the audit trail for exactly that case).
* **Snapshot** (never blocks; an ambiguous mapping is logged and left blank): `regulatory_registration`, `regulatory_newcode`,
  `regulatory_identifier`, `regulatory_manufacturer`, `regulatory_manufacturer_country`, `regulatory_registration_status`,
  `regulatory_registration_expiry`, `regulatory_coverage_date`, `regulatory_override`. With no mapping only the coverage date (and a
  covering override) are stored; the section stays visible because it is shown whenever a coverage date exists. Batch/serial/qty/UOM
  are not duplicated: they live on the row and its Serial and Batch Bundle.

## Alerts (`alerts.py`)

Daily `run_daily_expiry_processing`, weekly `send_weekly_regulatory_digest`.

* Refresh `days_to_expiry`; set Active -> Expired (registration, establishment) and Verified -> Expired (document) once a stated
  expiry has passed; refresh Item coverage for registrations that changed.
* Only registrations/establishments in status Active or Expired with `expiry_basis = Expiry Date Specified`, `renewal_status != Not
  Renewing` and no `superseded_by` are alerted. Documents with an expiry in status Verified/Expired are alerted for their registration.
* **Threshold rule**: use the tightest rule with `days_before_expiry >= days_remaining` (catch-up if a run was missed; looser
  thresholds are not replayed). One alert per (kind, reference, expiry date, threshold); a new expiry date re-arms alerts. After
  expiry, repeat on day 1 and every `overdue_repeat_days` (threshold stored as the negative days overdue, severity Expired).
* **FDA Query Due**: open actions in stage *FDA Query* alert at `fda_query_alert_days` before `response_due_date` and then
  overdue on the same repeat interval.
* A log that found no recipient (`No Recipients`) is retried on every run until someone can be notified; Queued/Failed logs are retried at
  most 3 times and a channel that already delivered (`system_sent` / `email_sent`) is not repeated. A superseded old document version
  stops alerting once a valid Verified document of the same registration, type and NEWCODE exists.
* Recipients: rule user, rule role members, the responsible user, and the enabled users in **Regulatory Settings > Alert
  Recipients** (they receive every expiry/FDA-query alert and the weekly digest; disabled users are skipped); falling back to the default owner, then Regulatory Approver users.
  Channels follow settings (system Notification Log, email). Logs in status Queued/Failed are retried on later runs (max 3).
* **Auto renewal action** (not created again for a cycle in which a Renewal action was Rejected or Cancelled): when `auto_create_renewal_action` and `days_remaining <= renewal_start_days_before_expiry`, create a
  Renewal action in stage Preparing unless an open (non Approved/Rejected/Cancelled) Renewal action already exists.

## Reports (Script Reports, module "Regulatory Affairs")

Regulatory Registration Expiry Summary, Regulatory Expiring Registration Affected Business, Regulatory Item Coverage
Matrix, Regulatory Compliance Gap, Regulatory Renewal Workbench. Product category is a filter on the Expiry Summary, Affected
Business, Compliance Gap and Renewal Workbench reports and a column on the Expiry Summary and Renewal Workbench. Compliance Gap
lists regulated Items (with a Product Category) without coverage (*Required Item Without Coverage*) and the other data gaps. All
read-only; they never act as a second policy engine. Roles: Regulatory Viewer/Operator/Approver, System Manager. All five use an RA DocType as `ref_doctype`, so RA users
need no Item permission. *Affected* Items are those with an Active product line that has not ended. Stock and open PO/SO quantities
are shown to every RA role; stock **value** only to users who can read Bin.

## Differences from `connect_diagnostics_core`

* Registrations can be **activated** (status control methods); the original had a read-only status and no way to set Active.
* Mapping resolution is window based, so renewals no longer create "ambiguous" mappings; approved renewals actually apply
  `new_expiry_date`, supersede predecessors and close their lines; later renewal cycles create new actions.
* Alerts catch up after a missed run, repeat after expiry, retry failures, skip dead registrations, also cover establishment licences
  and FDA Query due dates, and use system notifications as well as email.
* Returns, non-receipt stock entries and invoices of already-delivered goods are not blocked;
  all Thai FDA product categories are covered, not only medical devices;
  `expiry_basis` models certificates without an expiry.
* Verification is stamped once (not on every save) and restricted to Regulatory Approvers.
* Permissions live in the DocType JSON, not Custom DocPerms.

## Legacy data patch (Thai FDA Registration -> RA)

`erpnext.patches.v16_0.merge_thai_fda_into_regulatory_affairs` runs on `bench migrate` in `[post_model_sync]`, right after
`translate_thai_fda_to_english` (which turns Thai select values into English; the merge accepts both forms). It reads the legacy
tables with SQL only, is a no-op when they do not exist, and is idempotent. It never drops or deletes legacy data.

* It first calls `install.ensure_configuration()` so that the Custom Fields exist before Items are written.
* Legacy rows are grouped by the normalized registration number (spaces, punctuation and case ignored). A group becomes **one**
  Regulatory Product Registration named like the number on the newest row, with one registered-product line per legacy row (Item,
  product name, identifier = registration number, line status Active). A blank number becomes `LEGACY-<legacy name>`.
* Attributes come from the most recently modified row of the group. Status: Pending Approval -> Draft; Active, Suspended, Revoked,
  Cancelled, Superseded unchanged; an Active registration whose stated expiry has passed -> Expired. Category -> `product_category`
  (+ `other_category`), License / Notification Type -> `certificate_type`, activity, authority, holder, source URL, verified date ->
  `last_verified_on`, responsible user, issue/expiry dates and basis. Renewal status: Not Started, In Progress -> Preparing,
  Submitted, New Document Received -> Approved. Renewal Log rows -> `legacy_renewal_history`. An attached legacy `document` becomes a
  *Certificate* Regulatory Document (Verified when the legacy verified date is set, else Pending).
* Records are inserted with validation skipped on purpose (legacy data may break new rules such as overlapping mappings); the patch
  fills the derived fields itself and keeps the legacy `creation`, `modified` and `owner`. Rows are committed per registration; a
  failure rolls back that registration only and the run continues.
* **Overlapping registrations of one Item** (the same Item on lines of two different-number groups whose validity windows overlap and
  whose planned status is Active/Expired/Superseded) would make coverage ambiguous. The patch resolves them deterministically and only
  ever changes the lines it is about to create; nothing that already exists in RA is modified. Ranking, best first: planned status
  (Active beats Expired, which beats Superseded), then the registration that expires last (open-ended counts as last), then the newest
  legacy `modified`, then the **higher** registration/legacy name. The best-ranked line stays untouched. A losing line that starts before
  the winner's start is **trimmed** (`effective_to` = the winner's start minus one day, still Active, as RA's own successor handling
  does); a loser with no span left (inside the winner's span, or starting on the same day or later) becomes *Superseded*. Both get the
  remark "Overlaps <registration>; resolved automatically by the Thai FDA merge, review manually". The patch also checks the Active lines
  of in-force RA registrations already in the database: a planned line loses (is trimmed or superseded) against one that ranks at least
  as well, and is left alone against an existing Expired/Superseded registration when the planned one is Active. Every action (trimmed or
  superseded) is listed in the dry-run plan and in the logged conflicts. If the registration that **won** an overlap fails to insert, its
  losers were already trimmed or superseded: the run prints and returns (`result["warnings"]`, also logged) a warning naming the Items and
  the loser registrations. Fix the cause, re-run, and review those lines by hand (restore the line status or the end date).
* **Placeholder numbers**: records in status Pending Approval, or whose number has no digit ("-", "N/A", "TBA", "pending") or is empty
  after normalization, are never grouped by number; each becomes its own `LEGACY-<legacy name>` registration (the typed number is kept in
  the line remarks).
* Disagreements inside a group (different expiry, status, category, license type, holder, activity, issue date, duplicate Items) and skipped groups (number already used by an
  existing RA registration, partially merged groups) are printed and logged as an Error Log titled *Thai FDA to RA migration
  conflicts*. Review them and fix the registrations by hand.
* Idempotency: groups whose legacy names are already in some registration's `legacy_thai_fda_records` are skipped, so the patch can be
  re-run.
* Items: the category of the merged registrations (those listing legacy records) is copied to `regulatory_product_category` (and
  `regulatory_other_category`) of Items where it is blank, which makes those Items regulated. Items whose legacy assessment status (the
  `custom_thai_fda_applicability` column) says *Not Required / Exempt* get no category, so they stay unchecked. An Active line of an
  Active registration decides first, then Expired/Superseded, then Draft or other registrations; ties go to the newest `modified`. The
  copy runs in its own step that is committed before the settings step, and a failure is recorded in the run result. Until a copy
  succeeds every run recomputes it from all merged registrations, so a re-run repairs a missed copy; once one succeeds (a `DefaultValue`
  row with the key `regulatory_legacy_item_categories_copied`), a re-run copies only from the registrations it creates itself, so a
  category somebody cleared on purpose is not given back. Stored Item coverage is refreshed for the Items that got a category.
* Settings (only if the legacy settings exist): `enabled` -> `enable_expiry_alerts`, `overdue_repeat_days`,
  `send_system_notification`, `send_email` are copied once (a `DefaultValue` row with the key `regulatory_legacy_settings_merged` marks it done, so later edits are not overwritten); every legacy reminder day missing from the expiry alert rules is added
  (>= 120 days Info, 60-119 Warning, below 60 Critical, action *Reminder*); legacy recipients are added to *Alert Recipients*.
  Legacy reminder logs are not migrated. Recipients whose user no longer exists are skipped and listed. A failure in the settings step
  is logged and recorded in the run result without aborting the patch.
* Failed groups: the seeded configuration is committed first; if any group fails, the patch prints how many were NOT merged, where the
  details are and the re-run command (`bench --site <site> execute erpnext.patches.v16_0.merge_thai_fda_into_regulatory_affairs.execute`).
  It does not raise; re-running is safe and skips what is merged.

**Dry run** (prints the plan and writes nothing):

```
bench --site <site> execute erpnext.patches.v16_0.merge_thai_fda_into_regulatory_affairs.run --kwargs "{'dry_run': True}"
```

**What stays in the database.** The legacy DocType files, the report and the scheduler job are removed from the code, and
`bench migrate` deletes the orphaned legacy DocType *records*, but the tables `tabThai FDA Registration`, `tabThai FDA Renewal Log`,
`tabThai FDA Reminder Log`, `tabThai FDA Reminder Recipient` and the `Thai FDA Settings` rows in `tabSingles` stay untouched. After
verifying the migrated data you may drop the four tables manually (`DROP TABLE` on a backed-up site) and delete the `Thai FDA Settings`
rows in `tabSingles`; nothing in the code requires it. The Item columns `custom_thai_fda_applicability` and
`custom_thai_fda_assessment_note` also stay (see below). Take a backup before the first run.

## Retiring the Thai FDA Assessment Status

`erpnext.patches.v16_0.remove_thai_fda_assessment_status` runs in `[post_model_sync]` right after the merge patch, so the categories
copied from the merged registrations come first.

* While the Custom Field `custom_thai_fda_applicability` (and its column) still exists, it creates the RA Custom Fields if they are
  missing (the merge patch does so only when it has legacy registrations) and gives every Item assessed *Registration Required*
  (English or Thai value) that still has no Product Category the category **Other** with Other Category **Not specified (legacy)**, so
  it stays checked on transactions. It prints that count, and the count of Items assessed *Not Required / Exempt* that already have a
  Product Category (only possible when categories were set before this patch): those are checked from now on unless the category is
  cleared. Filter Items by Other Category = *Not specified (legacy)* and set their real category.
* It then deletes the Custom Fields `custom_thai_fda_applicability` and `custom_thai_fda_assessment_note` of Item (with their property
  setters). The columns and their values stay in `tabItem`; nothing is dropped. Items assessed *Not Required / Exempt* or *Not
  Assessed* without a category become unchecked Items; the exemption reasons remain only in the `custom_thai_fda_assessment_note`
  column.
* Re-running converts nothing because the status Custom Field is already gone, so a category cleared on purpose stays cleared.
* The two columns are no longer in the Item meta, so `bench trim-tables` would drop them with the old statuses and reasons. Export them
  first if you may need them; a later merge re-run without the status column also no longer recognises the exempt Items.

## Deploying

On a site designated for it, take a backup, run the dry run above against a copy if possible, and list site-only customisations that still use the retired Item fields (they need updating; the property setters of the
two fields themselves are deleted with them):

```sql
select 'Custom Field', name from `tabCustom Field` where concat_ws(' ', insert_after, depends_on, mandatory_depends_on,
	read_only_depends_on, fetch_from, `options`) like '%custom_thai_fda%'
union all select 'Property Setter', name from `tabProperty Setter` where concat_ws(' ', field_name, `value`) like '%custom_thai_fda%'
union all select 'Client Script', name from `tabClient Script` where script like '%custom_thai_fda%'
union all select 'Server Script', name from `tabServer Script` where script like '%custom_thai_fda%'
union all select 'Report', name from `tabReport` where concat_ws(' ', `json`, `query`, report_script) like '%custom_thai_fda%'
union all select 'Notification', name from `tabNotification` where concat_ws(' ', `condition`, message) like '%custom_thai_fda%';
```

Then run `bench --site <site> migrate`, which will

1. sync the RA DocTypes, reports and the workspace (the *Regulatory Affairs* Module Def and the three roles are created by the
   DocType sync itself);
2. run the post-model-sync patches: `translate_thai_fda_to_english`, then the merge patch (which calls
   `install.ensure_configuration`), then `remove_thai_fda_assessment_status`;
3. run `erpnext.regulatory_affairs.install.after_migrate`: ensures the roles, creates the Custom Fields on Item, the six transactions and
   their item rows, and seeds the expiry alert rules once (only when the settings have never been saved).

**Warning: first scheduler run.** The migrated registrations are new RA records, so the first daily run after migrating treats every
one inside the renewal window as new work: with *Auto Create Renewal Action* on (renewal window default 365 days) it creates a Renewal
Action for each, and with *Enable Expiry Alerts* on it sends catch-up alerts (system notifications and emails) for the tightest
threshold each registration has already crossed. To avoid the flood, switch off *Auto Create Renewal Action* and *Send Email* in Regulatory
Settings before the first scheduler run (and, if needed, *Enable Expiry Alerts*), review the migrated registrations, then switch them back on.

Fresh installs call the same setup from `erpnext.setup.install.after_install`. Restart the web/worker processes afterwards when they run
with `--preload`. Roll back by removing the hooks; the Custom Fields and DocTypes stay until deleted explicitly.

Suggested rollout: assign the roles; in Settings set every *Violation Policy* field to **Warning**; set the Product Category of the
Items that need a registration (first the ones with Other Category *Not specified (legacy)*); review the migrated registrations
(conflicts log, Draft/Expired ones, missing verified Certificates); watch the Compliance Gap report; then switch the policy fields to
**Block**. Every Item mapped on a merged legacy registration is regulated from the start (unless it was assessed Not Required / Exempt),
so with the default **Block** policies an expired or suspended legacy registration blocks its Items right after the migration.

## Acceptance scenarios (run on a designated test site)

1. Set a Product Category on an Item; a Purchase Receipt for it is blocked (no registration) and passes once a registration covers
   it. Clear the category again: the Purchase Receipt is not checked and the Item's coverage fields are empty after saving it.
2. Draft registration cannot be activated without a Verified Certificate and an Active product line; an Operator cannot verify the
   Certificate or activate; an Approver can.
3. Two Active registrations covering the same Item over overlapping dates are rejected; a renewed successor with a later issue date is
   accepted.
4. Renewal Action: Preparing -> Submitted -> FDA Query (needs due date) -> Approved with *New Expiry Date* extends the registration,
   reactivates an Expired one and sets the renewal status; with *Resulting Registration* the old one becomes Superseded and its lines end
   the day before the successor's issue date.
5. Daily job: an Active registration past its expiry becomes Expired, Item coverage clears, an Expired alert is logged once, and an
   overdue reminder repeats on day 1 and every *Overdue Reminder Interval* days.
6. Sales Invoice made from a Delivery Note keeps the delivery's snapshot even after the registration expires; a return is never blocked;
   a Stock Entry of type Material Transfer is not checked.
7. Override: the requester cannot approve; an approved override bound to one Delivery Note lets exactly that document through and is
   recorded on its rows.
8. Legacy data: on a copy of a site with Thai FDA Registration records, the dry run lists one registration per distinct number and the
   conflicts; the real run creates them (Active ones with a past expiry as Expired, legacy numbers kept, renewal log in *Renewal
   History*, certificate documents attached), shows the Items as covered, copies legacy reminder days and recipients into Settings, and a
   second run creates nothing new. Mapped Items get the registration's Product Category except those assessed *Not Required /
   Exempt*; Items assessed *Registration Required* without a registration get Other / *Not specified (legacy)*; the Item form no longer
   shows *Thai FDA Assessment Status* and the `tabItem` columns still hold the old values.

## Known limits

* The Item dashboard shows no Regulatory connection group; use the Item's *Current Registration* field and the *Coverage Matrix* button.

* Establishment licences get expiry alerts but no Renewal Actions; Registration Actions work on product registrations only.
* Purchase Invoice, Subcontracting and Stock Reconciliation are not guarded; Purchase Order and Sales Order only warn.
* Overrides are requested from the Override form (or API); the transactions have no shortcut button yet.
* Product lines of a live registration can still be edited by an Operator (history is kept by version tracking); the issue/expiry
  dates cannot.
* An Item's Product Category is not guarded: any user who may edit the Item can clear it and so take the Item out of the transaction
  checks (the change is kept in the Item's version history).
* The new code assumes `bench migrate` has run: submitting a guarded document on a site where the Settings DocType and the Item
  columns do not exist yet fails, like any other schema-dependent ERPNext change.
* **Not verified end to end.** The DocTypes, patches and reports are checked by unit tests with mocks; run the dry run and
  the whole scenario list on a designated test site before production.
* The merge validates nothing: overlapping mappings, duplicate numbers across groups or missing certificates in migrated data are
  surfaced by the reports and the conflicts log, not rejected. Partially merged groups and numbers already present in RA are skipped, not
  merged.
* Legacy reminder logs are not migrated (alert idempotency starts fresh, so the first daily run may send alerts again).
* Legacy tables remain in the database until dropped manually.
