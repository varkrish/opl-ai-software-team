/// <reference types="cypress" />
import React from 'react';
import { BrowserRouter } from 'react-router-dom';
import Agents from '../../src/pages/Agents';

describe('Agents Page', () => {
  beforeEach(() => {
    cy.intercept('GET', '/api/jobs', { fixture: 'jobs.json' }).as('getJobs');
    cy.intercept('GET', '/api/jobs/*/agents', { fixture: 'agents.json' }).as('getAgents');

    cy.mount(
      <BrowserRouter>
        <Agents />
      </BrowserRouter>
    );
  });

  it('should render the Crew Roster heading', () => {
    cy.contains('Crew Roster').should('be.visible');
  });

  it('should display 6 agent cards', () => {
    cy.wait('@getAgents');
    cy.contains('Meta Agent').should('be.visible');
    cy.contains('Product Owner').should('be.visible');
    cy.contains('Designer').should('be.visible');
    cy.contains('Tech Architect').should('be.visible');
    cy.contains('Dev Crew').should('be.visible');
    cy.contains('Frontend Crew').should('be.visible');
  });

  it('should show agent roles', () => {
    cy.wait('@getAgents');
    cy.contains('Orchestrator').should('be.visible');
    cy.contains('Requirements').should('be.visible');
    cy.contains('System Design').should('be.visible');
  });

  it('should show agent status labels', () => {
    cy.wait('@getAgents');
    cy.contains('working').should('be.visible');
    cy.contains('completed').should('be.visible');
    cy.contains('idle').should('be.visible');
  });

  it('should show model names', () => {
    cy.wait('@getAgents');
    cy.contains('deepseek-r1-distill-qwen-14b').should('be.visible');
    cy.contains('qwen3-14b').should('be.visible');
    cy.contains('granite-3-2-8b-instruct').should('be.visible');
  });

  it('should show last activity for active agents', () => {
    cy.wait('@getAgents');
    cy.contains('Generating SQL schema...').should('be.visible');
    cy.contains('Project initialized successfully').should('be.visible');
  });
});
