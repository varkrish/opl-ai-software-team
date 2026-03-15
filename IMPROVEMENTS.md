# Agentic Platform — Recommended Improvements

Based on the code review of `project-fc680c12`, the platform generates individual files with reasonable internal logic but consistently fails at **cross-file integration**: imports don't match exports, module paths are wrong, dependencies aren't declared, and different files assume incompatible APIs from each other.

The improvements below are organized by impact, targeting the root causes observed across runs.

---

## 1. Post-Generation Validation Pass (Highest Impact)

A **validation agent** should run after code generation and check cross-file contracts:

- **Import/export matching:** For every `require('X')` or `import { Y } from 'X'`, verify that file `X` exists and actually exports `Y` with that name and shape (default vs named).
- **Relative path correctness:** Validate that `require('./models/User')` resolves correctly from the file it's written in. This was broken in `auth.js`, `userController.js`, and both models.
- **Dependency declaration:** Scan all `require()` / `import` calls, collect the external packages used, and verify they're all listed in `package.json`.
- **Function signature alignment:** When `userRoutes.js` calls `userController.getProfile`, verify that `userController` actually exports a function named `getProfile`.

This alone would have caught ~12 of the 15 bugs in the reviewed run.

---

## 2. Shared Context / Contract File

Give all agents a **shared interface contract** they must conform to before generating code:

```
Auth middleware exports: default function (req, res, next)
User controller exports: { getProfile, updateProfile, createUser, login, ... }
Quiz model: default export from sequelize instance at ../config/db
API base path: /api/users, /api/quizzes
Auth header: Authorization: Bearer <token>
JWT env var: JWT_SECRET
```

Different agents independently decided export names, auth header conventions, and env var names. A shared contract would eliminate those inconsistencies.

---

## 3. Single-Stack Enforcement

Lock in the tech stack **before** code generation and validate conformance after:

- If the stack says "PostgreSQL + Sequelize", reject any file that imports `mongoose`.
- If the stack says "Vite + React", reject `<style jsx>` or CRA-style `process.env.REACT_APP_*`.
- If the frontend uses a QR scanning library, verify it exists on npm and exports what the code uses (`react_CAMERA` was a hallucinated package).

---

## 4. Dependency-Aware File Ordering

Generate files in dependency order rather than in parallel:

1. `config/db.js` first (establishes the sequelize instance)
2. Models next (they depend on the db instance)
3. Controllers (they depend on models)
4. Middleware (depends on models + jwt)
5. Routes (depend on controllers + middleware)
6. `server.js` last (wires everything together)

Each file should be generated with the **actual exports** of its dependencies visible, not assumed.

---

## 5. `package.json` as a First-Class Output

Generate `package.json` **after** all code files, by scanning the codebase for all `require()` and `import` statements. This guarantees completeness. Both reviewed runs had missing manifests or incomplete dependency lists.

---

## 6. Runnable Smoke Test

After generation, attempt:

```bash
cd backend && npm install && node -e "require('./server')"
cd frontend && npm install && npx vite build
```

If either fails, feed the error back to the agent for a fix pass. Even a basic "does it parse and resolve all imports" check would catch most issues.

---

## 7. Eliminate Hallucinated Libraries

Before writing an `import` for a third-party package, the agent should verify:

- The package exists on npm.
- The specific named exports used actually exist in that package's API.

`react_CAMERA`, `QRScannerView` from `react-qr-scanner`, and `express/json` as a standalone import were all hallucinated or incorrect.

---

## Priority Ranking

| Enhancement | Bugs It Would Catch | Effort |
|---|---|---|
| Import/export validation pass | ~12 of 15 | Medium |
| Shared interface contract | ~8 of 15 | Low |
| `package.json` from code scan | ~3 of 15 | Low |
| Single-stack enforcement | ~2 of 15 | Low |
| Smoke test | All 15 (safety net) | Medium |
| Library existence check | ~2 of 15 | Medium |
| Dependency-ordered generation | ~10 of 15 (preventive) | High |

---

## Recommended Starting Point

The **import/export validation pass** + **shared contract** + **package.json scan** is the highest-ROI trio — low-to-medium effort, and together they would catch nearly every bug observed across reviewed runs.
