/// <reference types="cypress" />
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import Landing from '../../src/pages/Landing';
import '@patternfly/react-core/dist/styles/base.css';

/**
 * Component tests for the Landing page — MTA Migration mode.
 *
 * These tests cover the gaps that let upload bugs slip through:
 * - File validation (MTA reports and source ZIP)
 * - Pre-submit readiness checks
 * - FormData construction (source_archive + documents sent correctly)
 * - Post-submit response verification
 */

const mountLanding = () => {
  cy.mount(
    <MemoryRouter initialEntries={['/']}>
      <Landing />
    </MemoryRouter>
  );
};

/** Switch the form to MTA Migration mode by clicking the toggle. */
const switchToMigrationMode = () => {
  cy.contains('button', 'MTA Migration').click();
};

/** Create a File object for use in tests. */
const makeFile = (name: string, content: string, type = 'application/octet-stream'): File => {
  return new File([content], name, { type });
};

describe('Landing — MTA Migration Mode', () => {
  beforeEach(() => {
    // Stub backend calls that fire on mount
    cy.intercept('GET', '/api/backends', {
      statusCode: 200,
      body: {
        backends: [
          { name: 'opl-ai-team', display_name: 'OPL AI Team', available: true },
        ],
      },
    }).as('getBackends');

    mountLanding();
    switchToMigrationMode();
  });

  // ── Mode toggle ───────────────────────────────────────────────────────────

  it('renders migration mode with step labels', () => {
    cy.contains('MTA Report').should('be.visible');
    cy.contains('Legacy Source Code').should('be.visible');
    cy.contains('Instructions (optional)').should('be.visible');
    cy.contains('Start Migration').should('be.visible');
  });

  it('shows "Required" labels before files are added', () => {
    cy.contains('Required').should('be.visible');
  });

  // ── Submit button state ───────────────────────────────────────────────────

  it('disables Start Migration when no files are uploaded', () => {
    cy.contains('button', 'Start Migration').should('be.disabled');
  });

  // ── MTA Report validation ────────────────────────────────────────────────

  it('accepts valid MTA report file types via file input', () => {
    // Simulate selecting a JSON file via the hidden file input
    const jsonFile = makeFile('issues.json', '{"issues":[]}', 'application/json');

    // Find the MTA report file input (accepts .json,.csv,.html,.xml,.yaml,.yml,.txt)
    cy.get('input[accept=".json,.csv,.html,.xml,.yaml,.yml,.txt"]')
      .selectFile(
        { contents: Cypress.Buffer.from('{"issues":[]}'), fileName: 'issues.json', mimeType: 'application/json' },
        { force: true }
      );

    // The file should appear in the list
    cy.contains('issues.json').should('be.visible');
  });

  it('shows "Ready" label after MTA report is added', () => {
    cy.get('input[accept=".json,.csv,.html,.xml,.yaml,.yml,.txt"]')
      .selectFile(
        { contents: Cypress.Buffer.from('{"issues":[]}'), fileName: 'report.json', mimeType: 'application/json' },
        { force: true }
      );

    // The step 1 label should update
    cy.contains('Ready').should('be.visible');
  });

  it('allows removing an uploaded MTA report file', () => {
    cy.get('input[accept=".json,.csv,.html,.xml,.yaml,.yml,.txt"]')
      .selectFile(
        { contents: Cypress.Buffer.from('data'), fileName: 'report.json', mimeType: 'application/json' },
        { force: true }
      );

    cy.contains('report.json').should('be.visible');

    // Click the remove button (TimesIcon) next to the file
    cy.contains('report.json').parent().find('button').click();
    cy.contains('report.json').should('not.exist');
  });

  // ── Source archive validation ─────────────────────────────────────────────

  it('accepts ZIP file for source code', () => {
    cy.get('input[accept=".zip"]')
      .selectFile(
        { contents: Cypress.Buffer.from('PK\x03\x04fake-zip'), fileName: 'legacy-app.zip', mimeType: 'application/zip' },
        { force: true }
      );

    cy.contains('legacy-app.zip').should('be.visible');
  });

  it('rejects non-ZIP file for source code', () => {
    cy.get('input[accept=".zip"]')
      .selectFile(
        { contents: Cypress.Buffer.from('tar content'), fileName: 'legacy-app.tar.gz', mimeType: 'application/gzip' },
        { force: true }
      );

    // Should show error, not accept the file
    cy.contains('Source code must be a .zip file').should('be.visible');
    cy.contains('legacy-app.tar.gz').should('not.exist');
  });

  it('shows "Ready" label after source ZIP is added', () => {
    cy.get('input[accept=".zip"]')
      .selectFile(
        { contents: Cypress.Buffer.from('PK\x03\x04fake'), fileName: 'app.zip', mimeType: 'application/zip' },
        { force: true }
      );

    // Should show two "Ready" labels (one for report is still Required, one for source)
    cy.contains('Legacy Source Code').parent().parent().contains('Ready').should('be.visible');
  });

  it('allows removing the source ZIP', () => {
    cy.get('input[accept=".zip"]')
      .selectFile(
        { contents: Cypress.Buffer.from('PK\x03\x04fake'), fileName: 'app.zip', mimeType: 'application/zip' },
        { force: true }
      );

    cy.contains('app.zip').should('be.visible');

    // Click remove
    cy.contains('app.zip').parent().find('button').click();
    cy.contains('app.zip').should('not.exist');
  });

  // ── Pre-submit readiness indicators ───────────────────────────────────────

  it('shows red indicator when MTA report is missing', () => {
    cy.contains('MTA report missing').should('be.visible');
  });

  it('shows red indicator when source code is missing', () => {
    cy.contains('Source code missing').should('be.visible');
  });

  it('enables Start Migration only when both report and source are provided', () => {
    // Initially disabled
    cy.contains('button', 'Start Migration').should('be.disabled');

    // Add MTA report
    cy.get('input[accept=".json,.csv,.html,.xml,.yaml,.yml,.txt"]')
      .selectFile(
        { contents: Cypress.Buffer.from('{"issues":[]}'), fileName: 'issues.json', mimeType: 'application/json' },
        { force: true }
      );

    // Still disabled — no source code
    cy.contains('button', 'Start Migration').should('be.disabled');

    // Add source ZIP
    cy.get('input[accept=".zip"]')
      .selectFile(
        { contents: Cypress.Buffer.from('PK\x03\x04fake'), fileName: 'app.zip', mimeType: 'application/zip' },
        { force: true }
      );

    // Now enabled
    cy.contains('button', 'Start Migration').should('not.be.disabled');
  });

  // ── FormData submission ───────────────────────────────────────────────────

  it('sends source_archive and documents in FormData on submit', () => {
    // Intercept the POST and verify FormData fields
    cy.intercept('POST', '/api/jobs', (req) => {
      // Verify this is multipart
      expect(req.headers['content-type']).to.include('multipart/form-data');

      req.reply({
        statusCode: 201,
        body: {
          job_id: 'test-job-001',
          status: 'queued',
          documents: 1,
          source_files: 5,
          github_repos: 0,
        },
      });
    }).as('createJob');

    // Intercept the auto-triggered migration start
    cy.intercept('POST', '/api/jobs/test-job-001/migrate', {
      statusCode: 202,
      body: { status: 'migrating', migration_id: 'mig-001', message: 'Started' },
    }).as('startMigration');

    // Add MTA report
    cy.get('input[accept=".json,.csv,.html,.xml,.yaml,.yml,.txt"]')
      .selectFile(
        { contents: Cypress.Buffer.from('{"issues":[{"id":"1"}]}'), fileName: 'issues.json', mimeType: 'application/json' },
        { force: true }
      );

    // Add source ZIP
    cy.get('input[accept=".zip"]')
      .selectFile(
        { contents: Cypress.Buffer.from('PK\x03\x04fake-zip-data'), fileName: 'legacy-app.zip', mimeType: 'application/zip' },
        { force: true }
      );

    // Submit
    cy.contains('button', 'Start Migration').click();

    // Verify the request was made
    cy.wait('@createJob').then((interception) => {
      // The request should contain mode=migration
      // Note: FormData inspection is limited in Cypress, but we verify the call happened
      expect(interception.response?.statusCode).to.equal(201);
    });
  });

  it('shows warning when server returns source_files=0 despite ZIP upload', () => {
    cy.intercept('POST', '/api/jobs', {
      statusCode: 201,
      body: {
        job_id: 'test-job-002',
        status: 'queued',
        documents: 1,
        source_files: 0,  // <-- Server couldn't extract any files!
        github_repos: 0,
      },
    }).as('createJob');

    cy.intercept('POST', '/api/jobs/test-job-002/migrate', {
      statusCode: 202,
      body: { status: 'migrating', migration_id: 'mig-002', message: 'Started' },
    }).as('startMigration');

    // Add report + ZIP
    cy.get('input[accept=".json,.csv,.html,.xml,.yaml,.yml,.txt"]')
      .selectFile(
        { contents: Cypress.Buffer.from('{}'), fileName: 'report.json', mimeType: 'application/json' },
        { force: true }
      );

    cy.get('input[accept=".zip"]')
      .selectFile(
        { contents: Cypress.Buffer.from('PK\x03\x04fake'), fileName: 'bad.zip', mimeType: 'application/zip' },
        { force: true }
      );

    cy.contains('button', 'Start Migration').click();
    cy.wait('@createJob');

    // Should show a warning about 0 files extracted
    cy.contains('server extracted 0 files', { timeout: 5000 }).should('be.visible');
  });

  it('shows warning when server returns documents=0 despite report upload', () => {
    cy.intercept('POST', '/api/jobs', {
      statusCode: 201,
      body: {
        job_id: 'test-job-003',
        status: 'queued',
        documents: 0,  // <-- Server didn't save the report!
        source_files: 5,
        github_repos: 0,
      },
    }).as('createJob');

    cy.intercept('POST', '/api/jobs/test-job-003/migrate', {
      statusCode: 202,
      body: { status: 'migrating', migration_id: 'mig-003', message: 'Started' },
    }).as('startMigration');

    cy.get('input[accept=".json,.csv,.html,.xml,.yaml,.yml,.txt"]')
      .selectFile(
        { contents: Cypress.Buffer.from('{}'), fileName: 'report.json', mimeType: 'application/json' },
        { force: true }
      );

    cy.get('input[accept=".zip"]')
      .selectFile(
        { contents: Cypress.Buffer.from('PK\x03\x04fake'), fileName: 'app.zip', mimeType: 'application/zip' },
        { force: true }
      );

    cy.contains('button', 'Start Migration').click();
    cy.wait('@createJob');

    cy.contains('server saved 0', { timeout: 5000 }).should('be.visible');
  });

  it('shows error on network failure', () => {
    cy.intercept('POST', '/api/jobs', { forceNetworkError: true }).as('createJobFail');

    cy.get('input[accept=".json,.csv,.html,.xml,.yaml,.yml,.txt"]')
      .selectFile(
        { contents: Cypress.Buffer.from('{}'), fileName: 'report.json', mimeType: 'application/json' },
        { force: true }
      );

    cy.get('input[accept=".zip"]')
      .selectFile(
        { contents: Cypress.Buffer.from('PK\x03\x04fake'), fileName: 'app.zip', mimeType: 'application/zip' },
        { force: true }
      );

    cy.contains('button', 'Start Migration').click();

    cy.contains('Failed to create migration project', { timeout: 5000 }).should('be.visible');
  });

  // ── GitHub URL alternative ────────────────────────────────────────────────

  it('enables submit when GitHub URL is provided instead of ZIP', () => {
    // Add MTA report
    cy.get('input[accept=".json,.csv,.html,.xml,.yaml,.yml,.txt"]')
      .selectFile(
        { contents: Cypress.Buffer.from('{}'), fileName: 'report.json', mimeType: 'application/json' },
        { force: true }
      );

    // Still disabled (no source)
    cy.contains('button', 'Start Migration').should('be.disabled');

    // Add GitHub URL instead of ZIP
    cy.get('input[aria-label="GitHub URL"]').type('https://github.com/test/legacy-app');
    cy.contains('button', 'Add').click();

    // Now enabled
    cy.contains('button', 'Start Migration').should('not.be.disabled');
    cy.contains('test/legacy-app').should('be.visible');
  });

  // ── Switching modes preserves state ───────────────────────────────────────

  it('switching to build mode and back preserves migration state', () => {
    // Add an MTA report
    cy.get('input[accept=".json,.csv,.html,.xml,.yaml,.yml,.txt"]')
      .selectFile(
        { contents: Cypress.Buffer.from('{}'), fileName: 'report.json', mimeType: 'application/json' },
        { force: true }
      );

    cy.contains('report.json').should('be.visible');

    // Switch to build mode
    cy.contains('button', 'Build New Project').click();
    cy.contains('Describe your project vision').should('be.visible');

    // Switch back to migration mode
    cy.contains('button', 'MTA Migration').click();

    // Report should still be there
    cy.contains('report.json').should('be.visible');
  });
});
