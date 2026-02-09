/// <reference types="cypress" />

describe('Task Monitoring', () => {
  beforeEach(() => {
    cy.intercept('GET', '/api/stats', { fixture: 'stats.json' });
    cy.intercept('GET', '/api/jobs', { fixture: 'jobs.json' });
    cy.intercept('GET', '/health', { fixture: 'health.json' });
    cy.intercept('GET', '/api/jobs/*/progress', { fixture: 'progress.json' });
    cy.intercept('GET', '/api/jobs/*/tasks', { fixture: 'tasks.json' }).as('getTasks');
    cy.intercept('GET', '/api/workspace/files*', { fixture: 'files.json' });
  });

  it('should display the Kanban board with tasks grouped by status', () => {
    cy.visit('/tasks');
    cy.wait('@getTasks');

    cy.contains('To Do').should('be.visible');
    cy.contains('In Progress').should('be.visible');
    cy.contains('Completed').should('be.visible');
  });

  it('should show task details within cards', () => {
    cy.visit('/tasks');
    cy.wait('@getTasks');

    cy.contains('Initialize project workspace').should('be.visible');
    cy.contains('Design database schema').should('be.visible');
    cy.contains('Define REST API endpoints').should('be.visible');
  });

  it('should show task phase labels', () => {
    cy.visit('/tasks');
    cy.wait('@getTasks');

    cy.contains('initialization').should('be.visible');
    cy.contains('schema').should('be.visible');
  });

  it('should update the dashboard stats', () => {
    cy.visit('/');

    cy.contains('Total Jobs').should('be.visible');
    cy.contains('5').should('be.visible');
    cy.contains('Running').should('be.visible');
    cy.contains('1').should('be.visible');
  });

  it('should show the progress bar on the dashboard', () => {
    cy.visit('/');

    cy.contains('Current Phase').should('be.visible');
    cy.contains('architecture').should('be.visible');
    cy.contains('45%').should('be.visible');
  });

  it('should show agents with correct statuses', () => {
    cy.intercept('GET', '/api/jobs/*/agents', { fixture: 'agents.json' }).as('getAgents');
    cy.visit('/agents');
    cy.wait('@getAgents');

    cy.contains('Tech Architect').should('be.visible');
    cy.contains('working').should('be.visible');
    cy.contains('Meta Agent').should('be.visible');
  });
});
