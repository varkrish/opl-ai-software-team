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

  it('should render main content area', () => {
    // AppLayout uses Outlet for route content; when mounted without Routes, main section still exists
    cy.get('.pf-v5-c-page__main-section').should('exist');
  });

  it('should render project breadcrumb', () => {
    cy.contains('opl-ai-software-team').should('be.visible');
  });

  it('should render user info in sidebar footer', () => {
    cy.contains('Admin User').should('be.visible');
    cy.contains('admin@redhat.com').should('be.visible');
  });

  it('should have the masthead with logo and toolbar sections', () => {
    cy.get('.pf-v5-c-masthead').should('exist');
  });

  it('should show Red Hat logo (brand image) in the masthead', () => {
    cy.get('.pf-v5-c-masthead img[alt="Red Hat"]').should('exist').and('be.visible');
  });

  it('should show admin user avatar in the sidebar footer', () => {
    cy.contains('Admin User').should('be.visible');
    cy.get('.pf-v5-c-avatar').should('exist').and('be.visible');
  });
});
