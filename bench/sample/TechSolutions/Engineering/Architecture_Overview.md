# FleetSync Architecture Overview

Owner: Priya Nair (VP of Engineering). Last reviewed: September 2026.

## Services
- **api-gateway** — Go, handles auth (OAuth 2.0 + SSO via Okta) and rate limiting (600 requests/min per customer).
- **dispatch-service** — Python (FastAPI), assigns loads to drivers.
- **route-optimizer** — Rust, computes routes; median response time 340 ms.
- **telematics-ingest** — Kafka consumers ingesting ~4.2M GPS pings per day.
- **web-app** — React + TypeScript customer dashboard.

## Data
- Primary database: PostgreSQL 16 on AWS RDS (us-east-2), Multi-AZ.
- Time-series GPS data: TimescaleDB, retained for 13 months.
- Backups: nightly snapshots, retained 35 days; restore tested quarterly.

## Infrastructure
- AWS EKS (Kubernetes 1.30), deployed with Argo CD from the `infra` repo.
- Monitoring: Datadog; on-call through PagerDuty (weekly rotation, handoff Mondays 10 AM CT).
