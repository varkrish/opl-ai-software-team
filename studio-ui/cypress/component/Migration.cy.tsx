import React from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Migration from '../../src/pages/Migration';
import '@patternfly/react-core/dist/styles/base.css';

const mountMigration = (jobId = 'test-job-123') => {
  cy.mount(
    <MemoryRouter initialEntries={[`/migration/${jobId}`]}>
      <Routes>
        <Route path="/migration/:jobId" element={<Migration />} />
      </Routes>
    </MemoryRouter>
  );
};

describe('Migration Page', () => {
  beforeEach(() => {
    // Default: no issues yet
    cy.intercept('GET', '/api/jobs/*/migration', {
      statusCode: 200,
      body: {
        job_id: 'test-job-123',
        summary: { total: 0, pending: 0, running: 0, completed: 0, failed: 0, skipped: 0 },
        issues: [],
      },
    }).as('getMigration');
  });

  it('renders migration form with goal and notes fields', () => {
    mountMigration();
    cy.get('[data-testid="migration-form"]').should('be.visible');
    cy.get('[data-testid="migration-goal-input"]').should('be.visible');
    cy.get('[data-testid="migration-notes-input"]').should('be.visible');
    cy.get('[data-testid="start-migration-btn"]').should('be.visible');
    cy.get('[data-testid="start-migration-btn"]').should('be.disabled');
  });

  it('enables start button when goal is entered', () => {
    mountMigration();
    cy.get('[data-testid="migration-goal-input"]').type('Migrate EAP 7 to 8');
    cy.get('[data-testid="start-migration-btn"]').should('not.be.disabled');
  });

  it('starts migration and shows progress table', () => {
    cy.intercept('POST', '/api/jobs/*/migrate', {
      statusCode: 202,
      body: { status: 'migrating', migration_id: 'mig-123', message: 'Started' },
    }).as('postMigrate');

    // After migration starts, return issues
    cy.intercept('GET', '/api/jobs/*/migration', {
      statusCode: 200,
      body: {
        job_id: 'test-job-123',
        summary: { total: 2, pending: 1, running: 1, completed: 0, failed: 0, skipped: 0 },
        issues: [
          {
            id: 'issue-001', title: 'Replace javax', severity: 'mandatory',
            effort: 'low', status: 'running', files: '["src/App.java"]',
            description: 'desc', migration_hint: 'hint', error: null,
            job_id: 'test-job-123', migration_id: 'mig-123',
            created_at: '2025-01-01', completed_at: null,
          },
          {
            id: 'issue-002', title: 'Update XML', severity: 'optional',
            effort: 'medium', status: 'pending', files: '["pom.xml"]',
            description: 'desc2', migration_hint: 'hint2', error: null,
            job_id: 'test-job-123', migration_id: 'mig-123',
            created_at: '2025-01-01', completed_at: null,
          },
        ],
      },
    }).as('getMigrationWithIssues');

    mountMigration();
    cy.get('[data-testid="migration-goal-input"]').type('EAP 7 to 8');
    cy.get('[data-testid="start-migration-btn"]').click();
    cy.wait('@postMigrate');

    // After polling kicks in
    cy.get('[data-testid="migration-issues-table"]', { timeout: 10000 }).should('be.visible');
    cy.contains('Replace javax').should('be.visible');
    cy.contains('Update XML').should('be.visible');
  });

  it('displays migration notes text area', () => {
    mountMigration();
    cy.get('[data-testid="migration-notes-input"]').should('be.visible');
    cy.get('[data-testid="migration-notes-input"]').type('Skip auth module');
    cy.get('[data-testid="migration-notes-input"]').should('have.value', 'Skip auth module');
  });

  it('shows error alert on API failure', () => {
    cy.intercept('POST', '/api/jobs/*/migrate', {
      statusCode: 400,
      body: { error: 'No documents uploaded' },
    }).as('postMigrateFail');

    mountMigration();
    cy.get('[data-testid="migration-goal-input"]').type('Upgrade');
    cy.get('[data-testid="start-migration-btn"]').click();

    // Error alert should appear
    cy.get('.pf-v5-c-alert', { timeout: 5000 }).should('be.visible');
  });

  it('shows summary badges when issues exist', () => {
    cy.intercept('GET', '/api/jobs/*/migration', {
      statusCode: 200,
      body: {
        job_id: 'test-job-123',
        summary: { total: 5, pending: 1, running: 0, completed: 3, failed: 1, skipped: 0 },
        issues: [
          {
            id: 'i-1', title: 'T', severity: 'mandatory', effort: 'low',
            status: 'completed', files: '[]', description: 'd', migration_hint: 'h',
            error: null, job_id: 'j', migration_id: 'm',
            created_at: '2025-01-01', completed_at: '2025-01-02',
          },
        ],
      },
    }).as('getSummary');

    mountMigration();
    cy.get('[data-testid="migration-summary"]', { timeout: 5000 }).should('be.visible');
    cy.contains('Total: 5').should('be.visible');
    cy.contains('Completed: 3').should('be.visible');
    cy.contains('Failed: 1').should('be.visible');
  });
});
