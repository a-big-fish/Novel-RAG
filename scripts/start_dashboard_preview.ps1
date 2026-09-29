param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$previousDatabase = [Environment]::GetEnvironmentVariable('POSTGRES_DB', 'Process')
Push-Location $repoRoot
try {
    # Keep the override in this process and its uvicorn child only.
    [Environment]::SetEnvironmentVariable('POSTGRES_DB', 'novel-rag-test-2', 'Process')
    uv run python -c "from sqlalchemy import inspect; from app.db.postgres import PostgresDatabase; db=PostgresDatabase.from_settings(); tables=set(inspect(db.engine).get_table_names()); db.dispose(); assert {'books', 'query_parsing_cache'} <= tables, 'preview database is missing required tables'"
    if ($LASTEXITCODE -ne 0) {
        throw 'Preview database check failed. Server was not started.'
    }
    Write-Host "Preview database: novel-rag-test-2; dashboard: http://127.0.0.1:$Port/dashboard"
    uv run uvicorn app.main:app --host 127.0.0.1 --port $Port
}
finally {
    [Environment]::SetEnvironmentVariable('POSTGRES_DB', $previousDatabase, 'Process')
    Pop-Location
}
