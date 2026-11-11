# Security Policy

## Supported Versions

Security fixes apply to the latest commit on the default branch of this open-source release.

## Reporting a Vulnerability

Please report suspected security vulnerabilities privately rather than opening a public issue:

- **Channel:** Use [GitHub Security Advisories](https://github.com/dummycodertech/VoiceKAM/security/advisories/new) (Private Vulnerability Reporting) on the repository instead of posting in public issues.
- **What to include:** Affected endpoint/file, minimal reproduction steps or proof-of-concept payload, and potential impact.

We aim to acknowledge reports within 72 hours and coordinate a fix before public disclosure.

## Built-In Protections

- **No persistent dataset storage:** Uploaded CSV/JSON seller tables remain strictly in RAM inside TTL-bounded LRU caches (`UploadedDatasetStore` and `SessionStore`, default TTL 2 hours, max 200 entries) and are never written to disk or application logs.
- **Input validation & XSS/formula safety:** Uploads reject payloads exceeding `MAX_UPLOAD_BYTES` (1 MB) or `MAX_UPLOAD_SKUS` (2,000 rows), block leading spreadsheet formula characters (`=`, `+`, `-`, `@`, `\t`, `\r`), and strip `<script>`, `<style>`, and HTML tags.
- **Public-demo quota & abuse controls:** Configurable sliding-window per-IP rate limiting (with first-hop `X-Forwarded-For` support), global `DAILY_TURN_CAP`, HTTP `413` body-size enforcement, configurable `CORS_ORIGINS`, and standard HTTP security response headers.
- **Secret isolation:** API keys are loaded strictly via environment variables (`.env` is gitignored) and never exposed through `/api/health` or SSE error payloads.
