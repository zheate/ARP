function Read-VerifiedDeployment {
    param([string]$Root, [string]$ExpectedHash)
    if ($ExpectedHash -notmatch '^[0-9a-fA-F]{64}$') { throw 'The installer must supply its embedded manifest SHA-256' }
    $manifestPath = Get-PayloadPath $Root 'deployment.json'
    if ((Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash -ine $ExpectedHash) { throw 'Deployment manifest SHA-256 mismatch' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.schemaVersion -ne 1 -or $manifest.architecture -ne 'x64' -or $manifest.approved -ne $true) { throw 'Unreviewed/unsupported deployment manifest' }
    $required = @('webview2','ch341-i2c','ch341-serial','ch343-serial','ocean-hr4','laserpoint-ftdi','ni-package-manager','ni-runtime','ophir','coherent')
    if (($manifest.components.id -join '|') -cne ($required -join '|')) { throw 'Incomplete or reordered component plan' }
    foreach ($file in $manifest.files) {
        $path = Get-PayloadPath $Root $file.path
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing offline payload: $($file.path)" }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $file.sha256) { throw "Payload SHA-256 mismatch: $($file.path)" }
    }
    return $manifest
}

function Assert-BuildSignatures {
    param([string]$Root, [object]$Manifest)
    # BUILD MACHINE ONLY: certificate chain/revocation verification may consult
    # the network. The disconnected target verifies anchored hashes; Windows
    # additionally validates catalog signatures during PnP driver installation.
    foreach ($file in $Manifest.files) {
        $path = Get-PayloadPath $Root $file.path
        $subject = if ($file.PSObject.Properties['signatureSubject']) { [string]$file.signatureSubject } else { '' }
        if ($subject -or [IO.Path]::GetExtension($path) -ieq '.cat') {
            $sig = Get-AuthenticodeSignature -LiteralPath $path
            if ($sig.Status -ne 'Valid' -or ($subject -and $sig.SignerCertificate.Subject -notmatch $subject)) { throw "Invalid build-time signature/publisher: $($file.path) ($($sig.Status))" }
        }
    }
    foreach ($component in $Manifest.components) {
        $infs = if ($component.kind -eq 'pnp') { @($component.inf) }
        elseif ($component.kind -eq 'vendor') { @($component.detect | Where-Object { $_.kind -eq 'driver-store' } | ForEach-Object { $_.inf }) }
        else { @() }
        foreach ($name in $infs) {
            $inf = Get-PayloadPath $Root $name
            # INF packages need a signed catalog, not a signature on the INF.
            $text = Get-Content -LiteralPath $inf -Raw
            $matches = [regex]::Matches($text, '(?im)^\s*CatalogFile(?:\.[^=\s]+)?\s*=\s*"?([^";\r\n]+)')
            if (-not $matches.Count) { throw "INF has no catalog: $name" }
            foreach ($match in $matches) {
                $catalog = Get-PayloadPath (Split-Path $inf -Parent) ($match.Groups[1].Value.Trim())
                if (-not (Test-Path -LiteralPath $catalog -PathType Leaf) -or (Get-AuthenticodeSignature -LiteralPath $catalog).Status -ne 'Valid') { throw "Missing/invalid catalog for $name" }
            }
        }
    }
}

function New-ProtectedDirectory {
    param([string]$Path)
    $full = [IO.Path]::GetFullPath($Path)
    [void](Get-PayloadPath ([IO.Path]::GetPathRoot($full)) ($full.Substring(3)))
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @('S-1-5-32-544', 'S-1-5-18')) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    if (Test-Path -LiteralPath $full) {
        # Cache/log roots are installer-owned, never application data folders.
        Set-Acl -LiteralPath $full -AclObject $acl
    } else {
        $directory = New-Object IO.DirectoryInfo($full)
        $directory.Create($acl)
    }
}

function Assert-MsiHash {
    param([string]$MsiPath, [object]$Manifest)
    if (-not (Test-Path -LiteralPath $MsiPath -PathType Leaf) -or (Get-FileHash -LiteralPath $MsiPath -Algorithm SHA256).Hash -ine $Manifest.application.sha256) { throw 'Power Test MSI is missing or does not match the deployment manifest' }
}

function Assert-NoUnresolvedInstaller {
    $root = Join-Path $env:ProgramData 'Power Test\InstallerCache'
    if (-not (Test-Path -LiteralPath $root)) { return }
    foreach ($directory in Get-ChildItem -LiteralPath $root -Directory -Force) {
        $marker = Get-PayloadPath $root ($directory.Name + '/process-state.json')
        if (Test-Path -LiteralPath $marker -PathType Leaf) {
            throw "An earlier installer was interrupted or timed out. State is unknown; maintenance must inspect $marker and the related logs before retrying. Never delete a cache used by an active installer."
        }
    }
}
