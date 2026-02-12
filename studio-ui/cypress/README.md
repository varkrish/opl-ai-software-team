# Studio UI – Cypress tests

## Running tests

- **Component tests:** `npm run cy:component`
- **E2E tests:** `npm run cy:e2e` (requires dev server and backend)
- **Interactive:** `npm run cy:open`

## What we cover

When you add or change UI behaviour, add or update tests here so we don’t regress.

### Files page (`component/Files.cy.tsx`)

- Files heading and Project Explorer
- File tree shows files from the **selected project**
- **Project dropdown:** changing the selected project **reloads the file tree** for that project (different project → different files)
- File click loads content; Refine button and panel; HTML preview iframe

### AppLayout (`component/AppLayout.cy.tsx`)

- Red Hat branding and “AI Crew”
- Sidebar nav (Dashboard, Tasks, Files, Settings)
- **Masthead** with **Red Hat logo** (brand image)
- **Admin user** label and **avatar** in sidebar footer
- Project breadcrumb

### Other

- Dashboard, Tasks, Agents: see their `*.cy.tsx` files.
- E2E: job creation, navigation, task monitoring in `e2e/`.

## Fixtures

- `jobs.json` – list of jobs (used by Files/Dashboard)
- `files.json` – workspace files for job-001
- `files-job-002.json` – workspace files for job-002 (used to assert project switch reloads tree)
