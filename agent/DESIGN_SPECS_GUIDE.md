# Design Specifications Guide

## Overview

You can provide design specification files to help the orchestrator plan and implement your software more accurately. These files are read and incorporated into the requirements analysis and development phases.

## How to Use

### Option 1: Command Line Flags

```bash
# Provide design specs directory
python src/ai_software_dev_crew/main.py "Build a REST API" --specs /path/to/design-specs

# Or use --design flag
python src/ai_software_dev_crew/main.py "Build a REST API" --design /path/to/design-specs

# Provide URLs as references
python src/ai_software_dev_crew/main.py "Build a REST API" --urls https://api.example.com/docs https://docs.example.com/architecture

# Or use --references or --refs flag
python src/ai_software_dev_crew/main.py "Build a REST API" --references https://api.example.com/docs

# Combine both files and URLs
python src/ai_software_dev_crew/main.py "Build a REST API" --specs ./design-specs --urls https://api.example.com/docs
```

### Option 2: Default Location

Place your design specification files in:
```
workspace/design-specs/
```

The system will automatically find and use them.

### Option 3: Interactive Mode

When running interactively, you'll be prompted:
```
📋 Design specs directory (optional, press Enter to skip):
🔗 Design spec URLs (comma-separated, optional, press Enter to skip):
```

### Option 4: URLs Only

You can provide only URLs without local files:
```bash
python src/ai_software_dev_crew/main.py "Build a REST API" --urls https://api.example.com/docs https://docs.example.com/spec
```

## Supported File Types

The system reads files with these extensions:
- `.md` - Markdown files (architecture docs, API docs, etc.)
- `.txt` - Text files
- `.yaml`, `.yml` - YAML configuration files
- `.json` - JSON files (API schemas, configs)
- `.py` - Python files (code examples, interfaces)
- `.js`, `.ts` - JavaScript/TypeScript files
- `.html`, `.css` - Web design files
- Files without extensions

## Recommended File Structure

```
design-specs/
├── architecture.md          # System architecture overview
├── api-spec.yaml            # API endpoint specifications
├── database-schema.sql       # Database schema
├── ui-design.md             # UI/UX requirements
├── technical-constraints.md  # Technical limitations/requirements
├── security-requirements.md  # Security specifications
└── deployment.md            # Deployment architecture
```

## What Gets Included

All files in the design-specs directory (and subdirectories) and URLs are:
1. Read/fetched and loaded into memory
2. Formatted and included in prompts to:
   - Business Analyst (for requirements analysis)
   - Developers (for implementation)
3. Used to inform:
   - Architecture decisions
   - API design
   - Database schema
   - Code structure
   - Technical constraints

## URL Support

You can provide URLs to:
- API documentation (OpenAPI/Swagger specs)
- Architecture diagrams (hosted markdown/docs)
- Design documents (Google Docs, GitHub, etc.)
- Technical specifications (any accessible URL)
- Code examples (GitHub gists, etc.)

**Note**: URLs must be publicly accessible or require no authentication. The system will fetch content using HTTP GET requests.

## Example Usage

### Example 1: API Specification

Create `design-specs/api-spec.yaml`:
```yaml
endpoints:
  - path: /api/v1/users
    method: GET
    description: List all users
    response:
      type: array
      items:
        type: object
        properties:
          id: integer
          name: string
          email: string
```

### Example 2: Architecture Document

Create `design-specs/architecture.md`:
```markdown
# System Architecture

## Overview
The system uses a microservices architecture with:
- API Gateway (FastAPI)
- User Service (Python)
- Database (PostgreSQL)

## Components
- API Layer: FastAPI REST API
- Business Logic: Service layer pattern
- Data Layer: SQLAlchemy ORM
```

### Example 3: Database Schema

Create `design-specs/schema.sql`:
```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
```

## How It Works

1. **Loading**: Design specs are loaded at orchestrator startup
2. **Formatting**: Files are formatted into a readable prompt format
3. **Inclusion**: Specs are included in:
   - BA Crew prompts (requirements analysis)
   - Dev Crew prompts (implementation)
4. **Reference**: Agents can reference specific files and sections

## Benefits

✅ **Better Planning**: BA can create more accurate requirements  
✅ **Consistent Implementation**: Developers follow your architecture  
✅ **Reduced Ambiguity**: Clear specifications reduce guesswork  
✅ **Faster Development**: Less back-and-forth clarification needed  
✅ **Quality Assurance**: Implementation matches design intent

## Tips

1. **Be Specific**: Include concrete examples and constraints
2. **Organize Well**: Use clear file names and structure
3. **Keep Updated**: Update specs as design evolves
4. **Include Examples**: Code examples help agents understand patterns
5. **Document Decisions**: Explain why certain choices were made

## Troubleshooting

### Specs Not Being Read
- Check file path is correct
- Verify file extensions are supported
- Ensure files are readable (permissions)

### Specs Not Being Used
- Check console output for "Design Specifications Loaded" message
- Verify files contain readable content
- Check error log for issues

### Large Files
- Break large files into smaller, focused files
- Use clear section headers
- Consider using YAML/JSON for structured data

