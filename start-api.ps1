$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& .\.venv\Scripts\python.exe -m uvicorn tradeforge.api:app --host 127.0.0.1 --port 8000
exit $LASTEXITCODE
