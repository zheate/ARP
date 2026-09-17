#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('arp-native-inventory-' + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $temporary)
try {
    $reportPath = Join-Path $temporary 'report.json'
    . (Join-Path $PSScriptRoot '..\Collect-OfflineTarget.ps1') -OutputPath $reportPath
    $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (@($report.nativeRuntimeFiles).Count -ne 2) { throw 'Expected DAQmx and VISA inventory records' }
    $kernel = Join-Path $env:SystemRoot 'System32\kernel32.dll'
    $exports = Read-NativeExportNames $kernel
    if ($exports.machine -ne '0x8664' -or -not $exports.names.Contains('GetLastError')) { throw 'Actual x64 PE export parsing failed' }
    $invalid = Join-Path $temporary 'invalid.dll'
    foreach ($content in @([byte[]]@(0, 1, 2, 3), [byte[]]@(77, 90))) {
        [IO.File]::WriteAllBytes($invalid, $content)
        $rejected = $false
        try { Read-NativeExportNames $invalid | Out-Null } catch { $rejected = $true }
        if (-not $rejected) { throw 'Malformed PE was accepted' }
    }
    # A DOS/PE header with a valid signature but an out-of-file export RVA.
    $content = New-Object byte[] 512
    [BitConverter]::GetBytes([uint16]0x5A4D).CopyTo($content, 0)
    [BitConverter]::GetBytes([uint32]64).CopyTo($content, 0x3C)
    [BitConverter]::GetBytes([uint32]0x4550).CopyTo($content, 64)
    [BitConverter]::GetBytes([uint16]0x8664).CopyTo($content, 68)
    [BitConverter]::GetBytes([uint16]240).CopyTo($content, 84)
    [BitConverter]::GetBytes([uint16]0x20B).CopyTo($content, 88)
    [BitConverter]::GetBytes([uint32]4096).CopyTo($content, 200)
    [IO.File]::WriteAllBytes($invalid, $content)
    $rejected = $false
    try { Read-NativeExportNames $invalid | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw 'Out-of-file RVA was accepted' }
    Write-Host 'Read-only inventory, native PE exports and malformed file checks passed; no vendor DLL loaded.'
} finally {
    $resolvedTemporary = [IO.Path]::GetFullPath($temporary)
    $temporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolvedTemporary.StartsWith($temporaryBase, [StringComparison]::OrdinalIgnoreCase) -and (Split-Path $resolvedTemporary -Leaf) -like 'arp-native-inventory-*') {
        Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force
    }
}
