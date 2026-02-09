/// <reference types="cypress" />
import { mount } from 'cypress/react18';
import '@patternfly/react-core/dist/styles/base.css';

declare global {
  namespace Cypress {
    interface Chainable {
      mount: typeof mount;
    }
  }
}

Cypress.Commands.add('mount', mount);
