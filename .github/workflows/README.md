# GitHub Actions Workflows

This directory contains GitHub Actions workflows for building and publishing container images to GitHub Container Registry (GHCR).

## Workflows

### 1. `build-and-push-image.yml`

**Purpose**: Builds and pushes container images on every push to main/develop branches and pull requests.

**Triggers**:
- Push to `main` or `develop` branches
- Version tags (e.g., `v1.0.0`)
- Pull requests
- Manual workflow dispatch

**Features**:
- Multi-platform builds (linux/amd64, linux/arm64)
- GitHub Actions cache for faster builds
- Automatic tagging based on branch/PR/tag
- Security scanning with Trivy

**Image Tags**:
- `latest` - Latest build from main branch
- `develop` - Latest build from develop branch
- `pr-{number}` - Builds for pull requests (not pushed)
- `{branch}-{sha}` - Branch-specific builds
- `v{version}` - Version tags

### 2. `release-image.yml`

**Purpose**: Builds and pushes release images when a GitHub release is created.

**Triggers**:
- GitHub release creation
- Manual workflow dispatch with version input

**Features**:
- Semantic versioning support
- Multiple tag formats (version, major.minor, major, latest)
- Release notes generation

**Image Tags**:
- `v{version}` - Exact version (e.g., `v1.0.0`)
- `{major}.{minor}` - Minor version (e.g., `1.0`)
- `{major}` - Major version (e.g., `1`)
- `latest` - Latest stable release

### 3. `nightly-build.yml`

**Purpose**: Builds a nightly image every day at 2 AM UTC.

**Triggers**:
- Scheduled (daily at 2 AM UTC)
- Manual workflow dispatch

**Features**:
- Daily automated builds
- Date-based tagging
- Keeps nightly builds up-to-date

**Image Tags**:
- `nightly` - Latest nightly build
- `nightly-{YYYYMMDD}` - Date-specific nightly build
- `nightly-{sha}` - Commit-specific nightly build

## Usage

### Pulling Images

```bash
# Latest stable
docker pull ghcr.io/OWNER/REPO:latest

# Specific version
docker pull ghcr.io/OWNER/REPO:v1.0.0

# Nightly build
docker pull ghcr.io/OWNER/REPO:nightly

# Branch-specific
docker pull ghcr.io/OWNER/REPO:develop
```

### Running the Container

```bash
# Basic run
docker run -p 8080:8080 ghcr.io/OWNER/REPO:latest

# With config file mount
docker run -p 8080:8080 \
  -v /path/to/config.yaml:/app/config.yaml:ro \
  -e CONFIG_FILE_PATH=/app/config.yaml \
  ghcr.io/OWNER/REPO:latest

# With workspace volume
docker run -p 8080:8080 \
  -v ./workspace:/app/workspace \
  ghcr.io/OWNER/REPO:latest
```

### Permissions

The workflows require the following GitHub permissions:
- `contents: read` - To checkout code
- `packages: write` - To push images to GHCR
- `security-events: write` - For security scanning

These are automatically granted via the `permissions` section in each workflow.

## Image Details

- **Base Image**: `python:3.11-slim`
- **Exposed Port**: `8080`
- **Health Check**: `/health/ready` endpoint
- **Default Command**: Runs the web UI on port 8080

## Security

- Images are scanned with Trivy for vulnerabilities
- Results are uploaded to GitHub Security tab
- Uses GitHub Actions cache for faster, more secure builds

## Troubleshooting

### Build Fails

1. Check the workflow logs in the Actions tab
2. Verify the Containerfile is correct
3. Ensure all dependencies are listed in `pyproject.toml`

### Image Not Found

1. Ensure the repository is public or you have access
2. Check that the workflow completed successfully
3. Verify the image name matches your repository

### Permission Denied

1. Ensure `GITHUB_TOKEN` has package write permissions
2. Check repository settings for package permissions
3. Verify the workflow has the correct permissions set
