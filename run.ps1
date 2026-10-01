$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    & python -m hd2_translate @args
    $pythonExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $pythonExitCode
