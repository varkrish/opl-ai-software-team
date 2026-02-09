/// <reference types="cypress" />

describe('Navigation', () => {
  beforeEach(() => {
    cy.intercept('GET', '/api/stats', { fixture: 'stats.json' });
    cy.intercept('GET', '/api/jobs', { fixture: 'jobs.json' });
    cy.intercept('GET', '/health', { fixture: 'health.json' });
    cy.intercept('GET', '/api/jobs/*/progress', { fixture: 'progress.json' });
    cy.intercept('GET', '/api/jobs/*/agents', { fixture: 'agents.json' });
    cy.intercept('GET', '/api/jobs/*/tasks', { fixture: 'tasks.json' });
    cy.intercept('GET', '/api/workspace/files*', { fixture: 'files.json' });

    cy.visit('/');
  });

  it('should load the Dashboard by default', () => {
    cy.contains('Mission Control').should('be.visible');
    cy.url().should('eq', Cypress.config().baseUrl + '/');
  });

  it('should navigate to Agents page', () => {
    cy.get('nav').contains('AI Crew').click();
    cy.url().should('include', '/agents');
    cy.contains('Crew Roster').should('be.visible');
  });

  it('should navigate to Tasks page', () => {
    cy.get('nav').contains('Tasks').click();
    cy.url().should('include', '/tasks');
    cy.contains('Task Board').should('be.visible');
  });

  it('should navigate to Files page', () => {
    cy.get('nav').contains('Files').click();
    cy.url().should('include', '/files');
    cy.contains('Project Explorer').should('be.visible');
  });

  it('should navigate back to Dashboard', () => {
    cy.get('nav').contains('Tasks').click();
    cy.contains('Task Board').should('be.visible');
    cy.get('nav').contains('Dashboard').click();
    cy.contains('Mission Control').should('be.visible');
  });

  it('should highlight the active nav item', () => {
    cy.get('nav').contains('AI Crew').click();
    cy.get('nav').find('.pf-m-current').should('contain', 'AI Crew');
  });
});
