/// <reference types="cypress" />
import React from 'react';
import { BrowserRouter } from 'react-router-dom';
import Tasks from '../../src/pages/Tasks';

describe('Tasks Page', () => {
  beforeEach(() => {
    cy.intercept('GET', '/api/jobs', { fixture: 'jobs.json' }).as('getJobs');
    cy.intercept('GET', '/api/jobs/*/tasks', { fixture: 'tasks.json' }).as('getTasks');

    cy.mount(
      <BrowserRouter>
        <Tasks />
      </BrowserRouter>
    );
  });

  it('should render the Task Board heading', () => {
    cy.contains('Task Board').should('be.visible');
  });

  it('should display 3 Kanban columns', () => {
    cy.wait('@getTasks');
    cy.contains('To Do').should('be.visible');
    cy.contains('In Progress').should('be.visible');
    cy.contains('Completed').should('be.visible');
  });

  it('should show task counts in column headers', () => {
    cy.wait('@getTasks');
    // To Do: registered(t6, t7, t8) = 3
    // In Progress: in_progress(t5) = 1
    // Completed: completed(t1, t2, t3) + skipped(t4) = 4
    cy.contains('To Do').parent().contains('3').should('be.visible');
    cy.contains('In Progress').parent().contains('1').should('be.visible');
    cy.contains('Completed').parent().contains('4').should('be.visible');
  });

  it('should show task descriptions', () => {
    cy.wait('@getTasks');
    cy.contains('Initialize project workspace').should('be.visible');
    cy.contains('Design database schema').should('be.visible');
  });

  it('should show the New Job button', () => {
    cy.contains('New Job').should('be.visible');
  });

  it('should open the New Job modal when clicking the button', () => {
    cy.contains('New Job').click();
    cy.contains('Create New Build Job').should('be.visible');
    cy.contains('Project Vision').should('be.visible');
    cy.contains('Start Build Job').should('be.visible');
  });

  it('should create a job when form is submitted', () => {
    cy.intercept('POST', '/api/jobs', {
      statusCode: 201,
      body: { job_id: 'new-job-123', status: 'queued' },
    }).as('createJob');

    cy.contains('New Job').click();
    cy.get('textarea').type('Build an e-commerce platform');
    cy.contains('Start Build Job').click();

    cy.wait('@createJob').its('request.body').should('deep.equal', {
      vision: 'Build an e-commerce platform',
    });
  });
});
