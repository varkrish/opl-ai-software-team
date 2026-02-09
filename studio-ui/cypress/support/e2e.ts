/// <reference types="cypress" />

// Custom commands for E2E tests

/**
 * Intercept all standard API calls with fixture data.
 * Call this at the start of each E2E test for consistent state.
 */
Cypress.Commands.add('interceptApi', () => {
  cy.intercept('GET', '/api/stats', { fixture: 'stats.json' }).as('getStats');
  cy.intercept('GET', '/api/jobs', { fixture: 'jobs.json' }).as('getJobs');
  cy.intercept('GET', '/health', { fixture: 'health.json' }).as('getHealth');
  cy.intercept('GET', '/api/jobs/*/progress', { fixture: 'progress.json' }).as('getProgress');
  cy.intercept('GET', '/api/jobs/*/agents', { fixture: 'agents.json' }).as('getAgents');
  cy.intercept('GET', '/api/jobs/*/tasks', { fixture: 'tasks.json' }).as('getTasks');
  cy.intercept('GET', '/api/workspace/files*', { fixture: 'files.json' }).as('getFiles');
});

declare global {
  namespace Cypress {
    interface Chainable {
      interceptApi(): Chainable<void>;
    }
  }
}
