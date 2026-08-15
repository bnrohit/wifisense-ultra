# Security Policy

## Scope

WiFiSense Ultra is a local research/automation platform. It is not intended to expose raw CSI ingestion directly to the public Internet.

## Deployment baseline

- Put TCP 8089 and the management UI on trusted networks.
- Use `WIFISENSE_API_TOKEN` for mutating API requests.
- Put remote UI access behind a TLS reverse proxy or VPN.
- Use authenticated MQTT and restrict broker ACLs to the configured topic prefix.
- Protect the `data/` directory because event history and occupancy/activity signals can be privacy-sensitive.
- Keep Python, ESP-IDF, base images, and dependencies patched.

## Reporting

Do not include real credentials, private CSI captures, household identities, or sensitive network details in a public issue. Provide a minimal reproduction with synthetic data where possible.

## Safety boundary

A security or software failure must not be interpreted as proof that a monitored person is safe. WiFiSense Ultra is not a certified alarm or medical system.
