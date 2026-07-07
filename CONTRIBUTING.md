# Contributing to TriceraDrive API

## Code of Conduct

This project is governed by the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating, you agree to uphold it.

## How to Contribute

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/amazing-feature`)
3. Make your changes
4. Run linting and tests
5. Commit with a descriptive message
6. Push and open a Pull Request

## Development Setup

```bash
uv sync
uv run uvicorn main:app --reload --port 8000
```

## Code Style

- Python 3.14+ with `from __future__ import annotations`
- Use `ruff` for linting and formatting
- Type hints required on all public functions
- Add `log_activity()` calls for new user-facing actions
