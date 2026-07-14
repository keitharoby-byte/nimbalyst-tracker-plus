[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$appRoot = Join-Path $env:APPDATA '@nimbalyst\electron'
$backendConfig = Join-Path $appRoot 'database-backend.json'
$databasePath = Join-Path $appRoot 'sqlite-db\nimbalyst.sqlite'
$nimbalystExe = Join-Path $env:LOCALAPPDATA 'Programs\Nimbalyst\Nimbalyst.exe'

$checks = [ordered]@{
  nimbalystExecutable = Test-Path -LiteralPath $nimbalystExe
  backendConfig = Test-Path -LiteralPath $backendConfig
  sqliteDatabase = Test-Path -LiteralPath $databasePath
  node = $null
  npm = $null
  python = $null
  backend = $null
  schemaCompatible = $false
  schemaFingerprint = $null
  readOnly = $false
}

$checks.node = (& node --version 2>$null)
$checks.npm = (& npm --version 2>$null)
$checks.python = (& python --version 2>&1)

if ($checks.backendConfig) {
  $checks.backend = (Get-Content -Raw -LiteralPath $backendConfig | ConvertFrom-Json).backend
}

if ($checks.sqliteDatabase -and $checks.backend -eq 'sqlite') {
  $env:NIMBALYST_PREFLIGHT_DATABASE = $databasePath
  $readerPath = Join-Path $root 'reader'
  $pythonReport = @'
import hashlib, json, os, sqlite3
from pathlib import Path

required = {
    "id", "issue_key", "type", "data", "workspace", "content",
    "archived", "type_tags", "deleted_at", "created", "updated",
}
path = Path(os.environ["NIMBALYST_PREFLIGHT_DATABASE"])
connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
try:
    connection.execute("PRAGMA query_only = ON")
    columns = connection.execute("PRAGMA table_info(tracker_items)").fetchall()
    names = {row[1] for row in columns}
    fingerprint = hashlib.sha256("|".join(f"{row[1]}:{row[2]}" for row in columns).encode()).hexdigest()
    read_only = connection.execute("PRAGMA query_only").fetchone()[0] == 1
    print(json.dumps({
        "schemaCompatible": required.issubset(names),
        "missingColumns": sorted(required - names),
        "schemaFingerprint": fingerprint,
        "readOnly": read_only,
    }))
finally:
    connection.close()
'@
  $report = $pythonReport | python - | ConvertFrom-Json
  $checks.schemaCompatible = $report.schemaCompatible
  $checks.schemaFingerprint = $report.schemaFingerprint
  $checks.readOnly = $report.readOnly
}

$checks | ConvertTo-Json -Depth 3

if (
  -not $checks.nimbalystExecutable -or
  $checks.backend -ne 'sqlite' -or
  -not $checks.sqliteDatabase -or
  -not $checks.schemaCompatible -or
  -not $checks.readOnly
) {
  exit 1
}
