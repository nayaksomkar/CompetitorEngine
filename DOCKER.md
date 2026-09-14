# Docker Setup — CompetitorEngine

CompetitorEngine is a pure orchestrator. It does **not** perform web search or
LLM analysis itself — it calls two sibling services:

| Service     | Image                | Host port | Container port | Purpose                          |
|-------------|----------------------|-----------|----------------|----------------------------------|
| llmping     | (LLMPing image)      | **8000**  | 8000           | LLM analysis endpoint            |
| webhunter   | `webhunter`          | **8765**  | 8000           | Web search / entity harvesting   |
| competitorengine | `competitorengine:latest` | **8001** | 8001    | Orchestrator (this repo)         |

> The orchestrator **refuses to start** unless both `LLMPING_URL` and
> `WEBHUNTER_URL` are set. This is intentional — a misconfigured orchestrator is
> worse than a failed one.

## Prerequisites

- Docker Engine ≥ 24
- The `llmping` and `webhunter` images already pulled / available locally
- On Linux/macOS, `host.docker.internal` is used so the orchestrator container
  can reach sibling services running on the host. Ensure Docker Desktop's
  "Allow host networking" is on, **or** add `--add-host=host.docker.internal:host-gateway`
  (Docker ≥ 20.10 on Linux).

## 1. Start the dependency services

```bash
# LLMPing — port 8000
docker run -d --name llmping -p 8000:8000 --restart unless-stopped \
    <your-llmping-image>

# WebHunter — port 8765 (mapped to its internal 8000)
docker run -d --name webhunter -p 8765:8000 --restart unless-stopped \
    webhunter
```

Verify both are reachable:

```bash
curl -sS -o /dev/null -w 'llmping  %{http_code}\n'   http://localhost:8000/
curl -sS -o /dev/null -w 'webhunter %{http_code}\n'   http://localhost:8765/
```

## 2. Build the orchestrator image

```bash
docker build -t competitorengine:latest .
```

## 3. Start the orchestrator

The orchestrator needs the URLs of the two services above. From inside Docker,
`localhost` refers to the container itself, so use `host.docker.internal`.

```bash
docker run -d --name competitorengine -p 8001:8001 --restart unless-stopped \
    -e LLMPING_URL=http://host.docker.internal:8000 \
    -e WEBHUNTER_URL=http://host.docker.internal:8765 \
    competitorengine:latest
```

Linux only — if `host.docker.internal` is not resolvable in your Docker setup,
add the gateway mapping:

```bash
docker run -d --name competitorengine -p 8001:8001 --restart unless-stopped \
    --add-host=host.docker.internal:host-gateway \
    -e LLMPING_URL=http://host.docker.internal:8000 \
    -e WEBHUNTER_URL=http://host.docker.internal:8765 \
    competitorengine:latest
```

## 4. Verify

```bash
# Container should report (healthy) within ~10s
docker ps --filter name=competitorengine \
    --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# Health endpoint
curl -sS -o /dev/null -w 'health %{http_code}\n' http://localhost:8001/health
```

## 5. Stop / reset

```bash
# Stop the orchestrator only (keep dependencies running)
docker stop competitorengine

# Tear everything down
docker rm -f competitorengine llmping webhunter
```

## Environment variables

| Variable          | Required | Default | Notes                                    |
|-------------------|----------|---------|------------------------------------------|
| `LLMPING_URL`     | yes      | —       | Full URL to the LLMPing service.         |
| `WEBHUNTER_URL`   | yes      | —       | Full URL to the WebHunter service.       |
| `LLMPING_TIMEOUT` | no       | 60      | Seconds per LLMPing request.             |
| `WEBHUNTER_TIMEOUT` | no     | 30      | Seconds per WebHunter request.           |
| `LLMPING_API_KEY` | no       | —       | Sent as `Authorization: Bearer ...`.      |
| `SERVICE_HOST`    | no       | 0.0.0.0 | Bind address inside the container.       |
| `SERVICE_PORT`    | no       | 8001    | Must match the published port.          |
| `LOG_LEVEL`       | no       | INFO    | DEBUG / INFO / WARNING / ERROR.          |

## Troubleshooting

**Container keeps restarting with `RuntimeError: LLMPING_URL is not set`**
You forgot `-e LLMPING_URL=...` (and/or `WEBHUNTER_URL=...`). Pass both.

**`curl http://localhost:8765/` returns 404**
That's expected if WebHunter doesn't expose a root route. Confirm via
`docker logs webhunter` and the actual API path (e.g. `/search`, `/entities`).

**Orchestrator can't reach `host.docker.internal` (Linux)**
Use the `--add-host=host.docker.internal:host-gateway` flag shown above, or run
all three containers on a shared user-defined bridge network and use service
names instead of `host.docker.internal`.
