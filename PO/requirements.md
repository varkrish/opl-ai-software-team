### High-Level Requirements
1. Create a REST API endpoint at `/health`.
2. The endpoint should return a JSON response with:
   - Service status (status: up/down)
   - Uptime in seconds
   - Dependency status (e.g., database, cache)
3. The response should be formatted in JSON.
4. The endpoint should be available with a response time of less than 500ms.
5. The endpoint should handle errors gracefully and return appropriate status codes (e.g., 500 if internal error).