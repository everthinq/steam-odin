# maFiles (local only)

This directory stores **your** Steam Desktop Authenticator `.maFile` exports.

- Files here are **gitignored** and must **never** be committed or published.
- Each file contains `shared_secret`, `identity_secret`, and session tokens that fully control the account.
- Import via the Heimdall UI; files are saved as `<steamid>.maFile`.

Keep backups offline (encrypted disk or password manager vault), not in the repository.
