"""
Frontend tests for migration job creation validation.

Tests that the UI properly validates migration job prerequisites and displays warnings.
"""
import { mount } from '@cypress/react';
import Landing from '../../src/pages/Landing';

describe('Migration Job Creation Validation', () => {
  beforeEach(() => {
    // Mock API client
    cy.intercept('GET', '/api/backends', {
      statusCode: 200,
      body: [{ name: 'opl-ai-team', display_name: 'OPL AI Team', available: true }]
    });
  });

  it('warns when MTA report uploaded but no source code', () => {
    mount(<Landing />);
    
    // Switch to migration mode
    cy.contains('MTA Migration').click();
    
    // Upload MTA report only
    const mtaReport = new File(['{"applicationId": "", "issues": {}}'], 'issues.json', { type: 'application/json' });
    cy.get('input[type="file"]').first().selectFile(
      { contents: mtaReport, fileName: 'issues.json' },
      { force: true }
    );
    
    // Verify MTA report shows as ready
    cy.contains('MTA Report').should('exist');
    cy.contains('Ready').should('be.visible');
    
    // Source code should show as "Required"
    cy.contains('Source Code').parent().within(() => {
      cy.contains('Required').should('be.visible');
    });
    
    // Start Migration button should be disabled
    cy.contains('Start Migration').should('be.disabled');
  });

  it('warns when source code uploaded but no MTA report', () => {
    mount(<Landing />);
    
    cy.contains('MTA Migration').click();
    
    // Upload source code only (via GitHub URL)
    cy.get('input[placeholder*="GitHub"]').type('https://github.com/user/repo');
    
    // Source code should show as ready
    cy.contains('GitHub URL added').should('be.visible');
    
    // MTA report should show as "Required"
    cy.contains('MTA Report').parent().within(() => {
      cy.contains('Required').should('be.visible');
    });
    
    // Start Migration button should be disabled
    cy.contains('Start Migration').should('be.disabled');
  });

  it('shows post-submit warning when backend reports 0 source files', () => {
    mount(<Landing />);
    
    cy.contains('MTA Migration').click();
    
    // Upload MTA report
    const mtaReport = new File(['[{"issues": {}}]'], 'issues.json', { type: 'application/json' });
    cy.get('input[type="file"]').first().selectFile(
      { contents: mtaReport, fileName: 'issues.json' },
      { force: true }
    );
    
    // Add source code
    cy.get('input[placeholder*="GitHub"]').type('https://github.com/user/repo');
    
    // Mock job creation response with 0 source files
    cy.intercept('POST', '/api/jobs', {
      statusCode: 201,
      body: {
        job_id: 'test-job-123',
        status: 'queued',
        documents: 1,
        source_files: 0,  // Backend reports 0 source files!
        github_repos: 1
      }
    }).as('createJob');
    
    // Submit
    cy.contains('Start Migration').click();
    
    cy.wait('@createJob');
    
    // Should show warning about 0 source files
    cy.contains('0 source files').should('be.visible');
    cy.contains('no source code', { matchCase: false }).should('be.visible');
  });

  it('shows post-submit warning when backend reports 0 documents', () => {
    mount(<Landing />);
    
    cy.contains('MTA Migration').click();
    
    // Add only source code (simulate forgetting MTA report)
    const sourceZip = new File(['PK'], 'source.zip', { type: 'application/zip' });
    cy.get('input[type="file"]').eq(1).selectFile(
      { contents: sourceZip, fileName: 'source.zip' },
      { force: true }
    );
    
    // Mock job creation response with 0 documents
    cy.intercept('POST', '/api/jobs', {
      statusCode: 201,
      body: {
        job_id: 'test-job-456',
        status: 'queued',
        documents: 0,  // Backend reports 0 documents!
        source_files: 10,
        github_repos: 0
      }
    }).as('createJob');
    
    // Force submit (in case validation is bypassed somehow)
    cy.contains('Start Migration').click({ force: true });
    
    cy.wait('@createJob');
    
    // Should show warning about 0 documents
    cy.contains('0 documents').should('be.visible');
    cy.contains('no MTA report', { matchCase: false }).should('be.visible');
  });

  it('blocks submit until both MTA report and source code provided', () => {
    mount(<Landing />);
    
    cy.contains('MTA Migration').click();
    
    // Initially disabled
    cy.contains('Start Migration').should('be.disabled');
    
    // Add MTA report - still disabled
    const mtaReport = new File(['[{"issues": {}}]'], 'issues.json', { type: 'application/json' });
    cy.get('input[type="file"]').first().selectFile(
      { contents: mtaReport, fileName: 'issues.json' },
      { force: true }
    );
    cy.contains('Start Migration').should('be.disabled');
    
    // Add source code - NOW enabled
    const sourceZip = new File(['PK'], 'source.zip', { type: 'application/zip' });
    cy.get('input[type="file"]').eq(1).selectFile(
      { contents: sourceZip, fileName: 'source.zip' },
      { force: true }
    );
    cy.contains('Start Migration').should('not.be.disabled');
  });

  it('prevents double submission of migration job', () => {
    mount(<Landing />);
    
    cy.contains('MTA Migration').click();
    
    // Upload files
    const mtaReport = new File(['[{"issues": {}}]'], 'issues.json', { type: 'application/json' });
    const sourceZip = new File(['PK'], 'source.zip', { type: 'application/zip' });
    
    cy.get('input[type="file"]').first().selectFile(
      { contents: mtaReport, fileName: 'issues.json' },
      { force: true }
    );
    cy.get('input[type="file"]').eq(1).selectFile(
      { contents: sourceZip, fileName: 'source.zip' },
      { force: true }
    );
    
    // Mock slow response
    cy.intercept('POST', '/api/jobs', (req) => {
      req.reply({
        statusCode: 201,
        body: { job_id: 'test-job-789', status: 'queued', documents: 1, source_files: 10, github_repos: 0 },
        delay: 2000  // 2 second delay
      });
    }).as('createJob');
    
    // Click submit
    cy.contains('Start Migration').click();
    
    // Button should be disabled during submission
    cy.contains('Start Migration').should('be.disabled');
    
    // Try clicking again - should be ignored
    cy.contains('Start Migration').click({ force: true });
    
    cy.wait('@createJob');
    
    // Should only make ONE API call
    cy.get('@createJob.all').should('have.length', 1);
  });

  it('validates file types for MTA report', () => {
    mount(<Landing />);
    
    cy.contains('MTA Migration').click();
    
    // Try to upload invalid file type
    const invalidFile = new File(['invalid'], 'report.exe', { type: 'application/octet-stream' });
    cy.get('input[type="file"]').first().selectFile(
      { contents: invalidFile, fileName: 'report.exe' },
      { force: true }
    );
    
    // Should show error
    cy.contains('invalid', { matchCase: false }).should('be.visible');
    cy.contains('.exe', { matchCase: false }).should('be.visible');
    
    // MTA report should still show as "Required"
    cy.contains('MTA Report').parent().within(() => {
      cy.contains('Required').should('be.visible');
    });
  });

  it('validates file size for MTA report (max 50MB)', () => {
    mount(<Landing />);
    
    cy.contains('MTA Migration').click();
    
    // Try to upload oversized file (mock 51MB)
    const oversizedFile = new File(['x'.repeat(51 * 1024 * 1024)], 'huge.json', { type: 'application/json' });
    cy.get('input[type="file"]').first().selectFile(
      { contents: oversizedFile, fileName: 'huge.json' },
      { force: true }
    );
    
    // Should show size error
    cy.contains('50 MB', { matchCase: false }).should('be.visible');
    cy.contains('too large', { matchCase: false }).should('be.visible');
  });

  it('validates ZIP format for source code', () => {
    mount(<Landing />);
    
    cy.contains('MTA Migration').click();
    
    // Try to upload non-ZIP file as source
    const nonZip = new File(['not a zip'], 'source.txt', { type: 'text/plain' });
    cy.get('input[type="file"]').eq(1).selectFile(
      { contents: nonZip, fileName: 'source.txt' },
      { force: true }
    );
    
    // Should reject non-ZIP
    cy.contains('.zip', { matchCase: false }).should('be.visible');
    cy.contains('only', { matchCase: false }).should('be.visible');
    
    // Source code should still show as "Required"
    cy.contains('Source Code').parent().within(() => {
      cy.contains('Required').should('be.visible');
    });
  });
});
