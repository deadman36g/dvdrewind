# Deployment & Self-Hosting Guide

## Deployment Philosophy
- **Strictly Private:** This archive is built for personal and private reference. It must not be exposed to the public internet.
- **Persistent Volume Isolation:** The SQLite database (`dvdrewind.db`) and raw source HTML files (`archive/raw/`) must reside on a mounted persistent volume.
- **Minimal Dependencies:** No external database engines (PostgreSQL, MySQL), search clusters (Elasticsearch), or caches (Redis) are needed. Everything runs within SQLite and a lightweight Python web server.

---

## 1. Local Run
To test or browse locally without Docker:
```powershell
cd C:\Users\deadman36g\.gemini\antigravity\scratch\dvdrewind
python -m src.cli search "Blade Runner"
python -m src.cli show 43651
```

---

## 2. Docker Compose (Planned for Phase 4)

A minimal `docker-compose.yml` mounts the persistent archive:

```yaml
version: '3.8'

services:
  dvdrewind-web:
    build: .
    container_name: dvdrewind-web
    restart: unless-stopped
    ports:
      - "127.0.0.1:8088:8088"
    volumes:
      - ./archive:/app/archive
    environment:
      - PORT=8088
      - HOST=0.0.0.0
```

> **Warning:** Before deploying to an existing homelab system, inspect target directories, Docker networks, and available ports rather than guessing.

---

## 3. Caddy Reverse Proxy Integration

When integrating behind an existing Caddy server, restrict access to private networks (LAN or Tailscale) or require authentication:

```caddy
dvdrewind.internal.lan {
    # Restrict to local subnet or Tailscale IP range
    @restricted {
        not remote_ip 192.168.1.0/24 100.64.0.0/10 127.0.0.1
    }
    respond @restricted "Access Denied" 403

    reverse_proxy 127.0.0.1:8088
}
```

Do not overwrite or edit the production Caddyfile without inspecting existing site blocks.
