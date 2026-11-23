# Windows PowerShell runner for the complete Meridian demo.
# Prerequisites: Python, PostgreSQL, and dependencies from requirements.txt.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Invoke-Python {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PythonArgs)
    & python @PythonArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed (exit code $LASTEXITCODE): python $($PythonArgs -join ' ')"
    }
}

$apiProcess = $null
try {
    if (Test-Path '.\data') { Remove-Item '.\data' -Recurse -Force }
    New-Item -ItemType Directory -Force -Path '.\data', '.\output' | Out-Null

    Write-Host '### 1. Generate messy source data'
    Invoke-Python -m sources.generate_data --out data

    Write-Host '### 2. Start the mock Ads API'
    $apiProcess = Start-Process -FilePath 'python' `
        -ArgumentList @('-m', 'sources.ads_api', '--data', 'data/ads_source.json', '--port', '5055') `
        -WorkingDirectory $PSScriptRoot -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput '.\output\api.log' -RedirectStandardError '.\output\api_error.log'

    $healthy = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            Invoke-RestMethod -Uri 'http://127.0.0.1:5055/health' -TimeoutSec 2 | Out-Null
            $healthy = $true
            break
        } catch {
            if ($apiProcess.HasExited) { throw 'Mock Ads API exited unexpectedly. Check output/api_error.log.' }
        }
    }
    if (-not $healthy) { throw 'Mock Ads API did not become healthy on port 5055.' }

    Write-Host '### 3. First full load'
    Invoke-Python -m meridian.pipeline --reset
    Write-Host '### 4. Re-run unchanged input (should load zero new rows)'
    Invoke-Python -m meridian.pipeline
   Write-Host '### 5. Simulate the next day of data'
Invoke-Python -m sources.simulate_next_day --data data

Write-Host '### Restart Ads API with updated source data'
if ($apiProcess -and -not $apiProcess.HasExited) {
    Stop-Process -Id $apiProcess.Id -Force
    $apiProcess.WaitForExit()
}

$apiProcess = Start-Process -FilePath 'python' `
    -ArgumentList @('-m', 'sources.ads_api', '--data', 'data/ads_source.json', '--port', '5055') `
    -WorkingDirectory $PSScriptRoot -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput '.\output\api.log' -RedirectStandardError '.\output\api_error.log'

$healthy = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:5055/health' -TimeoutSec 2 | Out-Null
        $healthy = $true
        break
    } catch {
        if ($apiProcess.HasExited) {
            throw 'Mock Ads API exited unexpectedly. Check output/api_error.log.'
        }
    }
}
if (-not $healthy) {
    throw 'Mock Ads API did not become healthy after restart.'
}

Write-Host '### 6. Incremental load'
Invoke-Python -m meridian.pipeline
    Write-Host '### 7. Re-run unchanged incremental input'
    Invoke-Python -m meridian.pipeline
    Write-Host '### 8. Generate evidence report'
    Invoke-Python -m meridian.report --markdown output/evidence.md
    Write-Host '### 9. Run tests (integration tests require PostgreSQL)'
    Invoke-Python -m pytest -q

    Write-Host "`nDemo completed successfully. Evidence: $((Join-Path $PSScriptRoot 'output/evidence.md'))" -ForegroundColor Green
}
finally {
    if ($apiProcess -and -not $apiProcess.HasExited) {
        Stop-Process -Id $apiProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
