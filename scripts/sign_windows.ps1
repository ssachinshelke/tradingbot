Param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [Parameter(Mandatory = $true)][string]$CertFile,
    [Parameter(Mandatory = $true)][string]$CertPassword,
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"

if (!(Test-Path $FilePath)) { throw "File not found: $FilePath" }
if (!(Test-Path $CertFile)) { throw "Certificate file not found: $CertFile" }

Write-Host "Signing file: $FilePath"
signtool sign /f $CertFile /p $CertPassword /fd SHA256 /tr $TimestampUrl /td SHA256 $FilePath
Write-Host "Sign completed."
