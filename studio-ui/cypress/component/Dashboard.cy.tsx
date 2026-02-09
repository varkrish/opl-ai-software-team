/// <reference types="cypress" />
import React from 'react';
import { BrowserRouter } from 'react-router-dom';
import Dashboard from '../../src/pages/Dashboard';

describe('Dashboard Page', () => {
  beforeEach(() => {
    cy.intercept('GET', '/api/stats', { fixture: 'stats.json' }).as('getStats');
    cy.intercept('GET', '/api/jobs', { fixture: 'jobs.json' }).as('getJobs');
    cy.intercept('GET', '/health', { fixture: 'health.json' }).as('getHealth');
    cy.intercept('GET', '/api/jobs/*/progress', { fixture: 'progress.json' }).as('getProgress');

    cy.mount(
      <BrowserRouter>
        <Dashboard />
      </BrowserRouter>
    );
  });

  it('should render the Mission Control heading', () => {
    cy.contains('Mission Control').should('be.visible');
  });

  it('should display stats cards after loading', () => {
    cy.wait('@getStats');
    cy.contains('Total Jobs').should('be.visible');
    cy.contains('5').should('be.visible');
    cy.contains('Running').should('be.visible');
    cy.contains('Completed').should('be.visible');
    cy.contains('Failed').should('be.visible');
  });

  it('should show system status', () => {
    cy.wait('@getHealth');
    cy.contains('System Status').should('be.visible');
    cy.contains('API Server').should('be.visible');
    cy.contains('healthy').should('be.visible');
  });

  it('should show current phase progress', () => {
    cy.wait('@getProgress');
    cy.contains('Current Phase').should('be.visible');
    cy.contains('architecture').should('be.visible');
  });

  it('should show crew activity feed', () => {
    cy.wait('@getProgress');
    cy.contains('Crew Activity').should('be.visible');
    cy.contains('Generating SQL schema').should('be.visible');
  });
});
