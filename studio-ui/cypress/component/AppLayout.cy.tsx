/// <reference types="cypress" />
import React from 'react';
import { BrowserRouter } from 'react-router-dom';
import AppLayout from '../../src/components/AppLayout';

describe('AppLayout', () => {
  beforeEach(() => {
    cy.mount(
      <BrowserRouter>
        <AppLayout>
          <div data-testid="page-content">Test Content</div>
        </AppLayout>
      </BrowserRouter>
    );
  });

  it('should render the Red Hat AI Crew branding', () => {
    cy.contains('AI Crew').should('be.visible');
  });

  it('should render the sidebar navigation', () => {
    cy.contains('Dashboard').should('be.visible');
    cy.contains('AI Crew').should('be.visible');
    cy.contains('Tasks').should('be.visible');
    cy.contains('Files').should('be.visible');
    cy.contains('Settings').should('be.visible');
  });

  it('should render child content', () => {
    cy.get('[data-testid="page-content"]').should('contain', 'Test Content');
  });

  it('should render project breadcrumb', () => {
    cy.contains('opl-ai-software-team').should('be.visible');
  });

  it('should render user info in sidebar footer', () => {
    cy.contains('Admin User').should('be.visible');
    cy.contains('admin@redhat.com').should('be.visible');
  });

  it('should have the Red Hat red masthead', () => {
    cy.get('.pf-v5-c-masthead').should('exist');
  });
});
