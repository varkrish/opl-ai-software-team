/// <reference types="cypress" />
import React from 'react';
import { BrowserRouter } from 'react-router-dom';
import Files from '../../src/pages/Files';

describe('Files Page', () => {
  beforeEach(() => {
    cy.intercept('GET', '/api/jobs', { fixture: 'jobs.json' }).as('getJobs');
    cy.intercept('GET', '/api/workspace/files*', (req) => {
      const jobId = req.url.includes('job_id=job-002') ? 'job-002' : 'job-001';
      const fixture = jobId === 'job-002' ? 'files-job-002.json' : 'files.json';
      req.reply({ fixture });
    }).as('getFiles');

    cy.mount(
      <BrowserRouter>
        <Files />
      </BrowserRouter>
    );
  });

  it('should render the Files heading', () => {
    cy.contains('Files').should('be.visible');
  });

  it('should show the Project Explorer panel', () => {
    cy.wait('@getFiles');
    cy.contains('Project Explorer').should('be.visible');
  });

  it('should display file tree after loading', () => {
    cy.wait('@getFiles');
    cy.contains('src').should('be.visible');
    cy.contains('README.md').should('be.visible');
    cy.contains('requirements.txt').should('be.visible');
  });

  it('should show empty state when no file is selected', () => {
    cy.contains('Select a file to view').should('be.visible');
  });

  it('should load file content when a file is clicked', () => {
    cy.intercept('GET', '/api/workspace/files/README.md*', {
      body: { path: 'README.md', content: '# Hello World' },
    }).as('getFileContent');

    cy.wait('@getFiles');
    cy.contains('README.md').click();
    cy.wait('@getFileContent');
    cy.contains('# Hello World').should('be.visible');
  });

  it('should show floating Refine button when a job is selected', () => {
    cy.wait('@getJobs');
    cy.wait('@getFiles');
    cy.get('button[aria-label="Refine with AI"]').should('be.visible');
    cy.contains('Refine').should('be.visible');
  });

  it('should open refine chat panel when clicking the floating button', () => {
    cy.intercept('GET', '/api/jobs/*/refinements', { body: [] }).as('getRefinements');
    cy.wait('@getJobs');
    cy.wait('@getFiles');
    cy.get('button[aria-label="Refine with AI"]').click();
    cy.contains('Refine with AI').should('be.visible');
    cy.get('textarea[placeholder*="Describe the change"]').should('exist');
  });

  it('should show HTML preview iframe with sandbox when an HTML file is selected', () => {
    cy.intercept('GET', '/api/workspace/files/index.html*', {
      body: { path: 'index.html', content: '<html><body>Hello</body></html>' },
    }).as('getHtmlFile');
    cy.wait('@getFiles');
    cy.contains('index.html').click();
    cy.wait('@getHtmlFile');
    cy.get('iframe[title="HTML preview"]').should('exist').and('have.attr', 'sandbox', 'allow-scripts');
  });

  it('should reload file tree when selecting a different project from the dropdown', () => {
    cy.wait('@getJobs');
    cy.wait('@getFiles');
    // Initial load shows job-001 files
    cy.contains('README.md').should('be.visible');
    cy.contains('requirements.txt').should('be.visible');

    // Open project dropdown and select the second job (calculator app)
    cy.get('[data-testid="files-project-select-toggle"]').click();
    cy.contains('Create a calculator app').click();

    // Wait for the new request for job-002 files and tree update
    cy.wait('@getFiles');
    // Tree should show job-002 files (package.json and lib are top-level in fixture)
    cy.contains('package.json').should('be.visible');
    cy.contains('lib').should('be.visible');
    // Old project-only files must be gone
    cy.contains('README.md').should('not.exist');
    cy.contains('requirements.txt').should('not.exist');
  });
});
