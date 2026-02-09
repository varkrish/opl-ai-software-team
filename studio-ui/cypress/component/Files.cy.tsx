/// <reference types="cypress" />
import React from 'react';
import { BrowserRouter } from 'react-router-dom';
import Files from '../../src/pages/Files';

describe('Files Page', () => {
  beforeEach(() => {
    cy.intercept('GET', '/api/jobs', { fixture: 'jobs.json' }).as('getJobs');
    cy.intercept('GET', '/api/workspace/files*', { fixture: 'files.json' }).as('getFiles');

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
});
