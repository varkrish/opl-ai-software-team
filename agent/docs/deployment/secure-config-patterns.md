# Secure Configuration Patterns for AI Software Development Crew

This guide provides deployment patterns for secure secret management across different environments.

## Overview

The AI Software Development Crew supports multiple secure configuration approaches:

1. **File-based configuration** with strict permissions (600/400)
2. **Docker secrets** for container deployments
3. **Kubernetes/OpenShift secrets** for orchestrated environments
4. **Encrypted configuration files** for at-rest encryption
5. **Environment variables** (legacy fallback)

## Configuration Priority Order

The system loads configuration from the following sources (highest to lowest priority):

1. CLI argument: `--config /path/to/config.yaml`
2. Environment variable: `CONFIG_FILE_PATH`
3. Project config: `./crew.config.yaml`
4. User config: `~/.crew-ai/config.yaml`
5. System config: `/etc/crew-ai/config.yaml`
6. Docker secrets: `/run/secrets/crew_config`
7. Kubernetes secrets: `/var/secrets/config.yaml`
8. Environment variables (legacy)
9. `.env` file (development only)

## Local Development

### User Configuration (Recommended)

```bash
# Create user config directory
mkdir -p ~/.crew-ai

# Copy example config
cp config.example.yaml ~/.crew-ai/config.yaml

# Set secure permissions (REQUIRED)
chmod 600 ~/.crew-ai/config.yaml

# Edit with your API key
vim ~/.crew-ai/config.yaml

# Run (auto-detects ~/.crew-ai/config.yaml)
python -m llamaindex_crew.main "Create a calculator"
```

### Project Configuration

```bash
# Create project-specific config
cp config.example.yaml ./crew.config.yaml
chmod 600 ./crew.config.yaml

# Edit with project-specific settings
vim ./crew.config.yaml

# Run (auto-detects ./crew.config.yaml)
python -m llamaindex_crew.main "Create a calculator"
```

### Explicit Config Path

```bash
# Use specific config file
python -m llamaindex_crew.main \
  --config /secure/path/config.yaml \
  "Create a calculator"
```

## Docker Deployments

### Option 1: Docker Secrets (Recommended)

Docker secrets provide secure secret storage for Swarm services:

```bash
# Create config file
cat > config.yaml << 'EOF'
llm:
  api_key: "your_actual_api_key"
  api_base_url: "https://litellm-prod.apps.maas.redhatworkshops.io"
  environment: "production"
# ... other settings
EOF

# Set permissions
chmod 600 config.yaml

# Create Docker secret
docker secret create crew_config config.yaml

# Remove local copy for security
rm config.yaml

# Run with secret (Swarm mode)
docker service create \
  --name crew-ai \
  --secret crew_config \
  -e CONFIG_FILE_PATH=/run/secrets/crew_config \
  -p 8080:8080 \
  crew-ai-software:latest
```

### Option 2: Volume Mount (Secure)

For standalone Docker containers:

```bash
# Create config in secure location
sudo mkdir -p /etc/crew-ai
sudo cp config.example.yaml /etc/crew-ai/config.yaml
sudo chmod 600 /etc/crew-ai/config.yaml
sudo chown your_user:your_group /etc/crew-ai/config.yaml

# Mount as read-only volume
docker run -d \
  --name crew-ai \
  -v /etc/crew-ai/config.yaml:/app/config.yaml:ro \
  -e CONFIG_FILE_PATH=/app/config.yaml \
  -p 8080:8080 \
  crew-ai-software:latest
```

### Option 3: Environment Variable Path

```bash
# Store config securely on host
export CONFIG_FILE_PATH=/secure/path/config.yaml

docker run -d \
  --name crew-ai \
  -v /secure/path:/secure/path:ro \
  -e CONFIG_FILE_PATH=$CONFIG_FILE_PATH \
  -p 8080:8080 \
  crew-ai-software:latest
```

## Kubernetes/OpenShift Deployments

### Using Kubernetes Secrets

```yaml
# Create secret from config file
apiVersion: v1
kind: Secret
metadata:
  name: crew-ai-config
  namespace: ai-dev
type: Opaque
data:
  config.yaml: |  # base64 encoded config content
    bGxtOgogIGFwaV9rZXk6ICJ5b3VyX2FwaV9rZXlfaGVyZSIKICAuLi4=
```

```bash
# Or create from file
kubectl create secret generic crew-ai-config \
  --from-file=config.yaml=/secure/path/config.yaml \
  -n ai-dev
```

```yaml
# Deployment with secret mount
apiVersion: apps/v1
kind: Deployment
metadata:
  name: crew-ai
  namespace: ai-dev
spec:
  replicas: 1
  selector:
    matchLabels:
      app: crew-ai
  template:
    metadata:
      labels:
        app: crew-ai
    spec:
      containers:
      - name: crew-ai
        image: crew-ai-software:latest
        ports:
        - containerPort: 8080
        env:
        - name: CONFIG_FILE_PATH
          value: "/var/secrets/config.yaml"
        volumeMounts:
        - name: config
          mountPath: /var/secrets
          readOnly: true
        securityContext:
          runAsNonRoot: true
          runAsUser: 1000
          allowPrivilegeEscalation: false
          readOnlyRootFilesystem: true
      volumes:
      - name: config
        secret:
          secretName: crew-ai-config
          defaultMode: 0400  # Read-only for owner
```

### Using OpenShift Secrets

```bash
# Create secret in OpenShift
oc create secret generic crew-ai-config \
  --from-file=config.yaml=/secure/path/config.yaml \
  -n ai-dev

# Deploy with secret
oc new-app crew-ai-software:latest \
  --name=crew-ai \
  -e CONFIG_FILE_PATH=/var/secrets/config.yaml

# Mount secret
oc set volume deployment/crew-ai \
  --add --type=secret \
  --secret-name=crew-ai-config \
  --mount-path=/var/secrets
```

## Red Hat Advanced Cluster Management (ACM) Integration

### Using External Secrets Operator

```yaml
# Install External Secrets Operator
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: acm-secret-store
  namespace: ai-dev
spec:
  provider:
    vault:
      server: "https://vault.example.com"
      path: "secret"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "crew-ai-role"

---
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: crew-ai-config
  namespace: ai-dev
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: acm-secret-store
    kind: SecretStore
  target:
    name: crew-ai-config
    creationPolicy: Owner
  data:
  - secretKey: config.yaml
    remoteRef:
      key: crew-ai/config
      property: config_yaml
```

## Encrypted Configuration Files

For additional security, encrypt sensitive values in config files:

### Generate Encryption Key

```bash
python -m llamaindex_crew.config.encrypt_tool --generate-key
# Output: wT6DPz... (store securely!)
```

### Encrypt API Key

```bash
python -m llamaindex_crew.config.encrypt_tool \
  --encrypt "your_actual_api_key" \
  --key "wT6DPz..."
# Output: gAAAAABf... (encrypted value)
```

### Config with Encrypted Values

```yaml
# config.encrypted.yaml
llm:
  api_key_encrypted: "gAAAAABf..."  # Encrypted value
  api_base_url: "https://litellm-prod.apps.maas.redhatworkshops.io"
  environment: "production"
```

### Use Encrypted Config

```bash
# Store encryption key securely (not in config file!)
export CONFIG_ENCRYPTION_KEY="wT6DPz..."

# Run with encrypted config
python -m llamaindex_crew.main \
  --config config.encrypted.yaml \
  "Create a calculator"
```

## Service Account Configuration

### Create Service Account User

```bash
# Create dedicated user (Linux)
sudo useradd -r -s /bin/false crew-ai

# Create config directory
sudo mkdir -p /etc/crew-ai
sudo cp config.example.yaml /etc/crew-ai/config.yaml

# Set ownership and permissions
sudo chown crew-ai:crew-ai /etc/crew-ai/config.yaml
sudo chmod 400 /etc/crew-ai/config.yaml  # Read-only

# Run as service account
sudo -u crew-ai python -m llamaindex_crew.main \
  --config /etc/crew-ai/config.yaml \
  "Create a calculator"
```

### Systemd Service

```ini
# /etc/systemd/system/crew-ai.service
[Unit]
Description=AI Software Development Crew
After=network.target

[Service]
Type=simple
User=crew-ai
Group=crew-ai
Environment="CONFIG_FILE_PATH=/etc/crew-ai/config.yaml"
ExecStart=/usr/bin/python3 -m llamaindex_crew.web.web_app
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable crew-ai
sudo systemctl start crew-ai
```

## Security Best Practices

### File Permissions

**Required permissions:**
- **600** (rw-------)  - Read/write by owner only (recommended for configs you edit)
- **400** (r--------) - Read-only by owner (recommended for production)

**Validation:**
```bash
# Check permissions
ls -l ~/.crew-ai/config.yaml
# Should show: -rw------- or -r--------

# Fix if needed
chmod 600 ~/.crew-ai/config.yaml
```

### Ownership

```bash
# Set owner to service account
sudo chown crew-ai:crew-ai /etc/crew-ai/config.yaml

# Verify
ls -l /etc/crew-ai/config.yaml
# Should show: crew-ai crew-ai
```

### Encryption at Rest

- Use `api_key_encrypted` instead of `api_key` in config files
- Store encryption key in environment variable or separate secure location
- Never commit encryption keys to version control
- Rotate encryption keys periodically

### Audit Logging

The system logs config source (but never secret values):

```
✅ Configuration loaded successfully from: /etc/crew-ai/config.yaml
☁️ Using Red Hat MaaS for worker agent
```

### Secret Rotation

```bash
# 1. Generate new API key from provider
# 2. Update config file
vim ~/.crew-ai/config.yaml

# 3. Verify permissions after edit
chmod 600 ~/.crew-ai/config.yaml

# 4. Test
python -m llamaindex_crew.main --show-config

# 5. Revoke old key
```

## Migration from Environment Variables

### Step 1: Create Config File

```bash
# Copy example
cp config.example.yaml ~/.crew-ai/config.yaml
chmod 600 ~/.crew-ai/config.yaml
```

### Step 2: Migrate Values

```bash
# From .env
LLM_API_KEY="sk-..."
LLM_API_BASE_URL="https://..."

# To config.yaml
llm:
  api_key: "sk-..."
  api_base_url: "https://..."
```

### Step 3: Test

```bash
# Verify config loads
python -m llamaindex_crew.main --show-config

# Test workflow
python -m llamaindex_crew.main "test vision"
```

### Step 4: Remove .env (Optional)

```bash
# After validation, can remove or keep for overrides
# Environment variables are lowest priority
rm .env  # optional
```

## Troubleshooting

### Permission Errors

```
ValueError: Config file has insecure permissions: 0o644
Required: 600 (rw-------) or 400 (r--------)
Fix with: chmod 600 /path/to/config.yaml
```

**Fix:**
```bash
chmod 600 ~/.crew-ai/config.yaml
```

### Config Not Found

```
ValueError: No configuration found. Please provide config via:
  1. --config argument
  2. CONFIG_FILE_PATH environment variable
  ...
```

**Fix:**
```bash
# Create config
cp config.example.yaml ~/.crew-ai/config.yaml
chmod 600 ~/.crew-ai/config.yaml
# Edit with your values
```

### Encryption Errors

```
ValueError: Failed to decrypt api_key_encrypted - invalid encryption key
```

**Fix:**
```bash
# Ensure encryption key is set
export CONFIG_ENCRYPTION_KEY="your_key_here"

# Or provide via CLI
python -m llamaindex_crew.main \
  --encryption-key "your_key" \
  --config config.encrypted.yaml \
  "vision"
```

### Docker Mount Issues

```
Error: cannot open /app/config.yaml: permission denied
```

**Fix:**
```bash
# Ensure file is readable
chmod 644 /host/path/config.yaml  # For container user

# Or use secrets (recommended)
docker secret create crew_config config.yaml
```

## Summary

**Recommended Approaches:**

| Environment | Method | Security Level |
|-------------|--------|----------------|
| Local Dev | `~/.crew-ai/config.yaml` (600) | Medium |
| Docker | Docker Secrets | High |
| Kubernetes | K8s Secrets + ReadOnly | High |
| OpenShift | OCP Secrets + SCC | Very High |
| Enterprise | External Secrets + Vault/ACM | Very High |
| Production | Service Account + 400 perms | High |

**Key Security Principles:**

1. **Never commit secrets** to version control
2. **Use file permissions** (600/400) to restrict access
3. **Run as service account** (not root)
4. **Use platform-native secrets** (Docker/K8s)
5. **Encrypt at rest** for sensitive environments
6. **Audit config loading** (logs show source)
7. **Rotate secrets** regularly

For more information, see:
- [Configuration Guide](../getting-started/configuration.md)
- [LLM Configuration](../guide/llm-configuration.md)
- [Security Best Practices](../security/best-practices.md)
