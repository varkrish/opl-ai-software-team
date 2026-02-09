/// <reference types="cypress" />

describe('Job Creation Flow', () => {
  beforeEach(() => {
    cy.intercept('GET', '/api/stats', { fixture: 'stats.json' });
    cy.intercept('GET', '/api/jobs', { fixture: 'jobs.json' });
    cy.intercept('GET', '/health', { fixture: 'health.json' });
    cy.intercept('GET', '/api/jobs/*/progress', { fixture: 'progress.json' });
    cy.intercept('GET', '/api/jobs/*/tasks', { fixture: 'tasks.json' });
    cy.intercept('GET', '/api/workspace/files*', { fixture: 'files.json' });
  });

  it('should create a new job from the Tasks page', () => {
    cy.intercept('POST', '/api/jobs', {
      statusCode: 201,
      body: { job_id: 'new-job-456', status: 'queued' },
    }).as('createJob');

    cy.visit('/tasks');
    cy.contains('Task Board').should('be.visible');

    // Open modal
    cy.contains('New Job').click();
    cy.contains('Create New Build Job').should('be.visible');

    // Fill form
    cy.get('textarea').type('Build a real-time chat application with WebSocket support');

    // Submit
    cy.contains('Start Build Job').click();
    cy.wait('@createJob');

    // Modal should close
    cy.contains('Create New Build Job').should('not.exist');
  });

  it('should prevent empty vision submission', () => {
    cy.visit('/tasks');
    cy.contains('New Job').click();

    // Start Build Job button should be disabled when textarea is empty
    cy.contains('Start Build Job').should('be.disabled');
  });

  it('should allow cancelling job creation', () => {
    cy.visit('/tasks');
    cy.contains('New Job').click();
    cy.contains('Create New Build Job').should('be.visible');

    cy.contains('Cancel').click();
    cy.contains('Create New Build Job').should('not.exist');
  });
});
