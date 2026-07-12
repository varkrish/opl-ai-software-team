# Solution Specification for Voyager – AI-Powered Family Travel Planner

## Technology Stack
| Layer | Technology | Reason |
|-------|------------|--------|
| Front-end | React 18 + Next.js 14 | Server-side rendering, API routes, Vercel deployment |
| Backend | Express (Node.js) | Thin API layer for travel and AI wrappers |
| Caching | Upstash Redis | Low-latency cache for AI responses and external API results |

## Caching Strategy (Redis)
1. **AI Cache** — Key: `ai:itinerary:{hash(constraints)}` → JSON, TTL 15 min.
2. **Flight/Hotel Cache** — Key: `amadeus:flights:{query}` → API response, TTL 30 min.

## Non-Goals
- No on-premise self-hosted Redis.
- No complex AI agents (LangChain).
