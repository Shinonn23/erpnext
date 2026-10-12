const path = require("path");
const fs = require("fs");
const evidenceRun = new Date().toISOString().replace(/[:.]/g, "-");
const { defineConfig } = require("../frappe/node_modules/cypress");

module.exports = defineConfig({
	testUser: "Administrator",
	video: true,
	retries: 0,
	defaultCommandTimeout: 20000,
	viewportWidth: 1400,
	viewportHeight: 1000,
	videosFolder: `cypress/artifacts/${evidenceRun}/videos`,
	screenshotsFolder: `cypress/artifacts/${evidenceRun}/screenshots`,
	e2e: {
		supportFile: "cypress/support/e2e.js",
		specPattern: "erpnext/loan/doctype/custody_loan/ui_test_*.js",
		testIsolation: true,
		setupNodeEvents(on, config) {
			on("task", {
				custodyEvidence({ name, evidence }) {
					const directory = path.join(__dirname, "cypress/artifacts", evidenceRun, "evidence");
					fs.mkdirSync(directory, { recursive: true });
					fs.writeFileSync(
						path.join(directory, `${name.replace(/[^a-zA-Z0-9_-]/g, "_")}.json`),
						JSON.stringify(evidence, null, 2)
					);
					return null;
				},
			});
			return config;
		},
	},
});
