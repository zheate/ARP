# Pure build metadata helpers; compatible with Windows PowerShell 5.1.
function Get-GpuiAppVersion {
    param([string]$ManifestPath)
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw
    $match = [regex]::Match($manifest, '(?m)^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"')
    if (-not $match.Success) { throw 'Native application version is missing.' }
    return $match.Groups[1].Value
}

function Write-GpuiBundleConfiguration {
    param([string]$ManifestPath, [string]$AssetsDirectory, [string]$OutputPath)
    $version = Get-GpuiAppVersion -ManifestPath $ManifestPath
    $resources = @{}
    $resources[[IO.Path]::GetFullPath($AssetsDirectory)] = 'assets/'
    $configuration = @{ version = $version; bundle = @{ resources = $resources } }
    $json = ConvertTo-Json -InputObject $configuration -Depth 5
    # PowerShell 5.1's Set-Content -Encoding UTF8 writes a BOM. JSON for the
    # installer CLI must have the same UTF-8 encoding on both PS versions.
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($OutputPath, $json, $encoding)
}

function Write-GpuiInstallerBuildStamp {
    param([string]$StampPath, [string]$Version, [string]$BinaryPath, [object[]]$Artifacts)
    if (-not $Artifacts -or $Artifacts.Count -eq 0) { throw 'No GPUI installer artifacts were produced.' }
    $outputs = @($Artifacts | ForEach-Object {
        [pscustomobject]@{ Name = $_.Name; SHA256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
    })
    $stamp = @{ Runtime = 'gpui'; Version = $Version; BinarySHA256 = (Get-FileHash -LiteralPath $BinaryPath -Algorithm SHA256).Hash; Outputs = $outputs }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($StampPath, (ConvertTo-Json -InputObject $stamp -Depth 5), $encoding)
}

function Assert-GpuiInstallerBuildStamp {
    param([string]$StampPath, [string]$Version, [string]$MsiPath)
    if (-not (Test-Path -LiteralPath $StampPath -PathType Leaf)) { throw 'GPUI installer build stamp is missing. Build the native application first.' }
    $stamp = Get-Content -LiteralPath $StampPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($stamp.Runtime -ne 'gpui' -or $stamp.Version -ne $Version) { throw 'Installer runtime or version does not match the GPUI build.' }
    $name = [IO.Path]::GetFileName($MsiPath)
    $entry = @($stamp.Outputs | Where-Object Name -eq $name)
    if ($entry.Count -ne 1 -or (Get-FileHash -LiteralPath $MsiPath -Algorithm SHA256).Hash -ne $entry[0].SHA256) { throw 'MSI is not a verified artifact of this GPUI build.' }
}
