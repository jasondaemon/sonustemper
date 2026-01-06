# Security Posture (SonusTemper)

## Perimeter
- All access is meant to flow through the bundled nginx proxy (Basic Auth + shared proxy secret).
- The app service itself should **not** publish ports directly; only the proxy should be exposed.
- Do not expose this directly to the public Internet. Use TLS termination (reverse proxy) or a VPN if you need remote access.

## Auth model
- Single-tenant, shared credentials (Basic Auth). No user accounts/roles.
- Shared proxy secret (`PROXY_SHARED_SECRET`) is used between proxy and app so API/UI calls don’t need an API key.
- API key (`API_KEY`) is optional and intended only for CLI/scripts; the UI does not use it.

## Deployment notes
- Change default credentials before first run: `BASIC_AUTH_PASS` and `PROXY_SHARED_SECRET`.
- Keep `.env` private; copy from `.env.example` and set your own secrets.
- For remote access, run behind TLS (reverse proxy) or inside a VPN; this stack does not include public TLS termination.
