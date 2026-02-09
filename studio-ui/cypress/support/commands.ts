/// <reference types="cypress" />

// Reusable custom commands

/**
 * Navigate to a page via sidebar nav and verify the URL
 */
Cypress.Commands.add('navigateTo', (label: string, expectedPath: string) => {
  cy.get('nav').contains(label).click();
  cy.url().should('include', expectedPath);
});

declare global {
  namespace Cypress {
    interface Chainable {
      navigateTo(label: string, expectedPath: string): Chainable<void>;
    }
  }
}
