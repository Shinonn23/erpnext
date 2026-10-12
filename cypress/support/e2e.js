// Frappe's e2e support suppresses uncaught exceptions; import only its commands.
import "../../../frappe/cypress/support/commands";

let evidence;
beforeEach(() => {
	evidence = { network: [], javascriptErrors: [] };
	cy.intercept("/api/**", (request) => {
		// Persist only method/path/status: payloads and headers can contain credentials.
		request.on("after:response", (response) => {
			evidence.network.push({
				method: request.method,
				path: request.url.split("?")[0],
				status: response.statusCode,
			});
		});
	});
});
Cypress.on("uncaught:exception", (error) => {
	evidence?.javascriptErrors.push({ name: error.name, message: error.message });
	// Returning undefined preserves Cypress's default failure behavior.
});
afterEach(function () {
	cy.task("custodyEvidence", {
		name: `${Cypress.spec.name}-${this.currentTest.title}`,
		evidence: { ...evidence, state: this.currentTest.state },
	});
});
