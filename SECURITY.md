# Security policy

## Supported versions

Only the latest released version is supported. Version 0.4.1 is validated for
the Windows SQLite backend in Nimbalyst 0.68.1.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability that could expose
tracker content or local filesystem information. Use GitHub's private security
advisory reporting for this repository. Include the Nimbalyst version, platform,
extension version, reproduction steps, and the smallest safe diagnostic output.

This extension deliberately has no tracker/database write path. It can write
explicitly requested `.ntimeline` and `.md` files inside the active workspace.
Reports involving tracker writes should identify whether the behavior came from
Nimbalyst's built-in tracker tools or another extension.
