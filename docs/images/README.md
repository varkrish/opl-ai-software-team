# Screenshots for documentation

Screenshot images used by the markdown docs in `docs/`.

## Images in this folder

| File | Description | Used in |
|------|-------------|--------|
| `dashboard.png` | Dashboard with jobs table, search, status filter, sortable columns, pagination | [dashboard-and-ui.md](../dashboard-and-ui.md) |
| `files.png` | Files page with job selector and file tree | [dashboard-and-ui.md](../dashboard-and-ui.md) |
| `landing.png` | Landing page / create job | [platform-overview.md](../platform-overview.md) |
| `refine.png` | Refine panel (slide-out) with prompt and file scope | [REFINEMENT_AND_UI.md](../REFINEMENT_AND_UI.md) |
| `refactor.png` | Refactor page with job list and pagination | [dashboard-and-ui.md](../dashboard-and-ui.md) |
| `migration-page.png` | Migration page with goal/notes and issues table | [dashboard-and-ui.md](../dashboard-and-ui.md), [migration.md](../migration.md) |

## Adding more screenshots

1. Run the app: `make studio-run` and `make studio-dev`; open http://localhost:3000.
2. Capture the view and save as PNG in this folder.
3. Reference in markdown: `![Alt text](images/filename.png)`.

See [SCREENSHOTS.md](../SCREENSHOTS.md) for the full list and capture notes.
