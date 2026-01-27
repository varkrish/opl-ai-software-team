# Health Check Endpoints

The AI Software Development Crew web application provides comprehensive health check endpoints for monitoring and production readiness.

## Endpoints

### `/health` - Basic Health Check

Quick health check to verify the service is running.

**Response: 200 OK**
```json
{
  "status": "healthy",
  "service": "AI Software Development Crew",
  "version": "1.0.0",
  "timestamp": "2024-01-27T10:30:00.000Z"
}
```

**Usage:**
```bash
curl http://localhost:8080/health
```

### `/health/ready` - Readiness Check

Comprehensive readiness check that verifies all critical dependencies.

**Checks:**
- ✅ Configuration loaded successfully
- ✅ Workspace is writable
- ✅ LLM is initialized
- ✅ Job storage is accessible

**Response: 200 OK (Ready)**
```json
{
  "status": "ready",
  "timestamp": "2024-01-27T10:30:00.000Z",
  "checks": {
    "config": {
      "status": "healthy",
      "message": "Configuration loaded successfully",
      "llm_environment": "production"
    },
    "workspace": {
      "status": "healthy",
      "message": "Workspace is writable",
      "path": "/app/workspace"
    },
    "llm": {
      "status": "healthy",
      "message": "LLM initialized successfully",
      "provider": "configured"
    },
    "job_storage": {
      "status": "healthy",
      "message": "Job storage accessible",
      "active_jobs": 3
    }
  }
}
```

**Response: 503 Service Unavailable (Not Ready)**
```json
{
  "status": "not_ready",
  "timestamp": "2024-01-27T10:30:00.000Z",
  "checks": {
    "config": {
      "status": "unhealthy",
      "message": "Configuration error: No configuration found"
    },
    "workspace": {
      "status": "healthy",
      "message": "Workspace is writable"
    },
    "llm": {
      "status": "skipped",
      "message": "Skipped due to config error"
    },
    "job_storage": {
      "status": "healthy",
      "message": "Job storage accessible"
    }
  }
}
```

**Usage:**
```bash
curl http://localhost:8080/health/ready
```

### `/health/live` - Liveness Check

Simple liveness check to verify the process is running (for Kubernetes liveness probes).

**Response: 200 OK**
```json
{
  "status": "alive",
  "timestamp": "2024-01-27T10:30:00.000Z"
}
```

**Usage:**
```bash
curl http://localhost:8080/health/live
```

### `/health/llm` - Deep LLM Check

Deep health check that actually tests LLM connectivity with a real API call.

**⚠️ Note:** This endpoint makes an actual LLM API call and may incur costs.

**Response: 200 OK (Healthy)**
```json
{
  "status": "healthy",
  "timestamp": "2024-01-27T10:30:00.000Z",
  "checks": {
    "config": {
      "status": "healthy",
      "llm_environment": "production",
      "api_base_url": "https://litellm-prod.apps.maas.redhatworkshops.io"
    },
    "llm_connectivity": {
      "status": "healthy",
      "message": "LLM responded successfully",
      "response_time_seconds": 0.847,
      "response_preview": "OK"
    }
  }
}
```

**Response: 503 Service Unavailable (Unhealthy)**
```json
{
  "status": "unhealthy",
  "timestamp": "2024-01-27T10:30:00.000Z",
  "checks": {
    "config": {
      "status": "healthy",
      "llm_environment": "production"
    },
    "llm_connectivity": {
      "status": "unhealthy",
      "message": "LLM connection failed: Connection timeout",
      "error_type": "TimeoutError"
    }
  }
}
```

**Usage:**
```bash
curl http://localhost:8080/health/llm
```

## Kubernetes Integration

### Liveness Probe

Use `/health/live` for liveness probes to check if the pod is alive:

```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8080
  initialDelaySeconds: 30
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 3
```

### Readiness Probe

Use `/health/ready` for readiness probes to check if the pod is ready to serve traffic:

```yaml
readinessProbe:
  httpGet:
    path: /health/ready
    port: 8080
  initialDelaySeconds: 10
  periodSeconds: 5
  timeoutSeconds: 3
  successThreshold: 1
  failureThreshold: 3
```

### Startup Probe

Use `/health/ready` for startup probes to check if the application has started:

```yaml
startupProbe:
  httpGet:
    path: /health/ready
    port: 8080
  initialDelaySeconds: 0
  periodSeconds: 10
  timeoutSeconds: 3
  failureThreshold: 30  # 5 minutes total
```

### Complete Deployment Example

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: crew-ai
  namespace: ai-dev
spec:
  replicas: 2
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
          name: http
        env:
        - name: CONFIG_FILE_PATH
          value: "/var/secrets/config.yaml"
        volumeMounts:
        - name: config
          mountPath: /var/secrets
          readOnly: true
        
        # Liveness probe - is the process alive?
        livenessProbe:
          httpGet:
            path: /health/live
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
          timeoutSeconds: 5
          failureThreshold: 3
        
        # Readiness probe - ready to serve traffic?
        readinessProbe:
          httpGet:
            path: /health/ready
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 5
          timeoutSeconds: 3
          successThreshold: 1
          failureThreshold: 3
        
        # Startup probe - has the app started?
        startupProbe:
          httpGet:
            path: /health/ready
            port: 8080
          initialDelaySeconds: 0
          periodSeconds: 10
          timeoutSeconds: 3
          failureThreshold: 30
        
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
      
      volumes:
      - name: config
        secret:
          secretName: crew-ai-config
          defaultMode: 0400
```

## Docker Health Checks

The Containerfile includes a health check using `/health/ready`:

```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8080/health/ready || exit 1
```

**Check Docker container health:**
```bash
docker ps
# Look for "healthy" or "unhealthy" in STATUS column

docker inspect --format='{{.State.Health.Status}}' container_name
```

## Monitoring & Alerting

### Prometheus Metrics (Future)

Health check endpoints can be integrated with Prometheus for monitoring:

```yaml
# ServiceMonitor for Prometheus Operator
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: crew-ai
spec:
  selector:
    matchLabels:
      app: crew-ai
  endpoints:
  - port: http
    path: /health/ready
    interval: 30s
```

### Alert Rules (Example)

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: crew-ai-alerts
spec:
  groups:
  - name: crew-ai
    interval: 30s
    rules:
    - alert: CrewAIUnhealthy
      expr: probe_success{job="crew-ai-health"} == 0
      for: 5m
      labels:
        severity: critical
      annotations:
        summary: "Crew AI is unhealthy"
        description: "Health check has been failing for 5 minutes"
```

## Troubleshooting

### Check All Endpoints

```bash
#!/bin/bash
# health_check_all.sh

BASE_URL="http://localhost:8080"

echo "=== Basic Health ==="
curl -s $BASE_URL/health | jq

echo -e "\n=== Readiness Check ==="
curl -s $BASE_URL/health/ready | jq

echo -e "\n=== Liveness Check ==="
curl -s $BASE_URL/health/live | jq

echo -e "\n=== LLM Deep Check ==="
curl -s $BASE_URL/health/llm | jq
```

### Common Issues

#### Config Not Loaded

```json
{
  "checks": {
    "config": {
      "status": "unhealthy",
      "message": "Configuration error: No configuration found"
    }
  }
}
```

**Fix:**
```bash
# Ensure config is available
export CONFIG_FILE_PATH=~/.crew-ai/config.yaml
# Or mount config in Docker/Kubernetes
```

#### Workspace Not Writable

```json
{
  "checks": {
    "workspace": {
      "status": "unhealthy",
      "message": "Workspace error: Permission denied"
    }
  }
}
```

**Fix:**
```bash
# Fix permissions
chmod 755 /app/workspace
chown app-user:app-user /app/workspace
```

#### LLM Connection Failed

```json
{
  "checks": {
    "llm_connectivity": {
      "status": "unhealthy",
      "message": "LLM connection failed: Connection timeout"
    }
  }
}
```

**Fix:**
- Check API key is valid
- Verify network connectivity
- Check API base URL
- Verify firewall rules

## Load Balancer Configuration

### AWS Application Load Balancer

```json
{
  "HealthCheckPath": "/health/ready",
  "HealthCheckIntervalSeconds": 30,
  "HealthCheckTimeoutSeconds": 5,
  "HealthyThresholdCount": 2,
  "UnhealthyThresholdCount": 3,
  "Matcher": {
    "HttpCode": "200"
  }
}
```

### NGINX

```nginx
upstream crew_ai {
    server 127.0.0.1:8080;
    
    # Health check
    check interval=3000 rise=2 fall=3 timeout=1000 type=http;
    check_http_send "GET /health/ready HTTP/1.0\r\n\r\n";
    check_http_expect_alive http_2xx;
}
```

### HAProxy

```haproxy
backend crew_ai
    option httpchk GET /health/ready
    http-check expect status 200
    server crew1 127.0.0.1:8080 check inter 10s fall 3 rise 2
```

## Best Practices

### 1. Use Different Endpoints for Different Purposes

- **`/health/live`**: Kubernetes liveness probe (fast, no external deps)
- **`/health/ready`**: Kubernetes readiness probe (checks all deps)
- **`/health`**: Load balancer health checks (lightweight)
- **`/health/llm`**: Manual testing and detailed diagnostics (makes API calls)

### 2. Configure Appropriate Timeouts

```yaml
# Fast checks for liveness
livenessProbe:
  timeoutSeconds: 5
  failureThreshold: 3  # Kill pod after 15s

# More lenient for readiness
readinessProbe:
  timeoutSeconds: 10
  failureThreshold: 3  # Remove from service after 30s
```

### 3. Monitor Health Check Metrics

- Track health check response times
- Alert on repeated failures
- Log health check failures for debugging

### 4. Avoid Expensive Checks in Readiness

- Don't make LLM API calls in `/health/ready` (use `/health/llm` for that)
- Keep checks fast (< 1 second)
- Cache results if necessary

## Summary

| Endpoint | Purpose | Checks | Cost | Recommended Use |
|----------|---------|--------|------|-----------------|
| `/health` | Basic health | Service running | Free | Load balancer |
| `/health/ready` | Readiness | All deps initialized | Free | K8s readiness probe |
| `/health/live` | Liveness | Process alive | Free | K8s liveness probe |
| `/health/llm` | Deep LLM check | Actual LLM call | $$$ | Manual testing only |

**Next Steps:**
- Integrate with your monitoring system (Prometheus, Datadog, etc.)
- Set up alerts for health check failures
- Configure load balancer health checks
- Test failover scenarios

For more information, see:
- [Kubernetes Probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)
- [Docker Health Checks](https://docs.docker.com/engine/reference/builder/#healthcheck)
- [Configuration Guide](../getting-started/configuration.md)
