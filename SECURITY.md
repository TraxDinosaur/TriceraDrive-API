# Security Policy

## Reporting a Vulnerability

Open an issue on GitHub or contact the maintainer directly. Do not disclose vulnerabilities publicly until they are resolved.

## Security Practices

- App password is hashed with PBKDF2 (SHA-256, 100k iterations)
- MEGA sessions are encrypted at rest using Fernet (symmetric encryption)
- JWT tokens expire after 24 hours
- Pass the `MEGA_ENCRYPTION_KEY` environment variable in production — do not use the default
- The `data/` directory is gitignored; ensure it is not exposed in production
