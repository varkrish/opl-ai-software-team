import json
from crew_studio.asgi_app import app

openapi_schema = app.openapi()

with open("openapi.json", "w") as f:
    json.dump(openapi_schema, f, indent=2)

print("OpenAPI schema written to openapi.json")
