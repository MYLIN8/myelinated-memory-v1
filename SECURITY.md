# Security Policy

## Reporting Vulnerabilities
Please report any security vulnerabilities by opening an issue on GitHub. Do not disclose sensitive information publicly.

## License Check
- **License:** MIT
- **Dependencies:** None (Zero-dependency project)
- **Data Access:** Local file access only (`~/.hermes/memory/`). No network calls.
- **Injection Risks:** Sanitized user inputs via standard Python file operations.
