# Technology Stack

| Layer | Technology | Justification |
|-------|------------|---------------|
| Front-end framework | **React 18** | Component UI with Next.js |
| Application framework | **Next.js 14** | SSR and Vercel deployment |
| Server-side runtime | **Node.js 18** | Express and serverless functions |
| API layer | **Express** | Mandated Express router |
| Map visualisation | **React-Leaflet** | Interactive maps without requiring a proprietary mapping service or additional database tier. |
| Caching (edge) | **Vercel Edge Cache** | Short-lived caching without introducing a separate database tier. |
| Linting/Formatting | **ESLint + Prettier** | Code quality and consistent style |

<tech_stack>
project-root/
├── front-end/
│   ├── src/pages/index.tsx
│   └── package.json
├── api-layer/
│   ├── src/index.ts
│   └── package.json
├── caching/
│   └── redisClient.ts
</tech_stack>
