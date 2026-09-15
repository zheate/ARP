function Read-MsiRows {
    param([object]$Database, [string]$Sql, [int]$Columns)
    $view = $Database.OpenView($Sql)
    try {
        $view.Execute()
        while ($null -ne ($row = $view.Fetch())) {
            try {
                $values = @()
                for ($i = 1; $i -le $Columns; $i++) { $values += [string]$row.StringData($i) }
                [pscustomobject]@{ Values = $values }
            } finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($row) }
        }
    } finally { $view.Close(); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($view) }
}

function Get-ApplicationContract {
    param([string]$MsiPath, [string]$ExpectedVersion)
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $db = $null
    try {
        $db = $installer.OpenDatabase([IO.Path]::GetFullPath($MsiPath), 0)
        $properties = @{}
        foreach ($row in @(Read-MsiRows $db 'SELECT `Property`, `Value` FROM `Property`' 2)) { $properties[$row.Values[0]] = $row.Values[1] }
        if ($properties['ProductVersion'] -ne $ExpectedVersion) { throw "MSI version does not match release version $ExpectedVersion" }
        $directories = @(Read-MsiRows $db 'SELECT `Directory` FROM `Directory`' 1)
        if (-not ($directories | Where-Object { $_.Values[0] -ceq 'INSTALLDIR' })) { throw 'MSI does not expose the reviewed INSTALLDIR directory; no fake path override is allowed' }
        $components = @{}
        foreach ($row in @(Read-MsiRows $db 'SELECT `Component`, `ComponentId`, `Directory_`, `KeyPath` FROM `Component`' 4)) { $components[$row.Values[0]] = $row.Values }
        $files = @()
        foreach ($row in @(Read-MsiRows $db 'SELECT `File`, `Component_`, `FileName` FROM `File`' 3)) {
            $name = ($row.Values[2] -split '\|')[-1]
            if ($name -in @('tauri-ui.exe', 'arp-python.exe')) {
                $component = $components[$row.Values[1]]
                if ($component[2] -cne 'INSTALLDIR' -or $component[3] -cne $row.Values[0] -or -not $component[1]) { throw "MSI $name is not a key-path file directly under INSTALLDIR" }
                $files += [pscustomobject]@{ name = $name; componentId = $component[1] }
            }
        }
        if ($files.Count -ne 2 -or @($files.name | Select-Object -Unique).Count -ne 2) { throw 'MSI must contain exactly the main executable and the bundled Python sidecar' }
        return [pscustomobject]@{ version = $ExpectedVersion; productCode = $properties['ProductCode']; upgradeCode = $properties['UpgradeCode']; files = $files }
    } finally {
        if ($db) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($db) }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer)
    }
}

function Assert-ApplicationPath {
    param([string]$Path)
    if ($Path -notmatch '^[A-Za-z]:[\\/].+' -or $Path -match '[<>|"*?\x00-\x1f]' -or $Path.Substring(2) -match ':') { throw 'Choose an absolute local application directory, not a drive root or network share' }
    foreach ($part in $Path.Substring(3) -split '[/\\]') {
        if ($part -in @('', '.', '..')) { throw 'Application directory cannot contain empty or dot segments' }
    }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    if (Test-Path -LiteralPath $full -PathType Leaf) { throw 'Application directory is an existing file' }
    if ($full.Length -gt 200) { throw 'Choose an application path no longer than 200 characters' }
    foreach ($segment in $full.Substring(3) -split '[/\\]') {
        if ($segment -in @('', '.', '..') -or $segment -match '[ .]$|^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') { throw 'Invalid application path segment' }
    }
    foreach ($reserved in @($env:SystemRoot, $env:ProgramData)) {
        if ($reserved -and ($full -ieq $reserved -or $full.StartsWith($reserved.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase))) { throw 'Do not install the application in Windows or ProgramData' }
    }
    foreach ($base in @([Environment]::GetFolderPath('ProgramFiles'), [Environment]::GetFolderPath('ProgramFilesX86'))) {
        if ($base -and $full -ieq $base) { throw 'Choose a Power Test subdirectory, not the Program Files root' }
    }
    $drive = New-Object IO.DriveInfo -ArgumentList ($full.Substring(0, 3))
    if (-not $drive.IsReady -or $drive.DriveType -ne [IO.DriveType]::Fixed -or $drive.DriveFormat -ne 'NTFS') { throw 'Application path requires a ready local fixed NTFS volume' }
    [void](Get-PayloadPath ($full.Substring(0, 3)) ($full.Substring(3)))
    return $full
}

function Assert-ApplicationPreflight {
    param([string]$MsiPath, [string]$Directory, [object]$Contract)
    $actual = Get-ApplicationContract $MsiPath $Contract.version
    if ($actual.productCode -ne $Contract.productCode) { throw 'MSI ProductCode differs from embedded contract' }
    # Never kill an instrument controller to make an upgrade possible.
    if (@(Get-Process -Name 'tauri-ui', 'arp-python' -ErrorAction SilentlyContinue).Count) { throw 'Close Power Test safely before installation; the installer will not terminate device-control processes.' }
    $installer = New-Object -ComObject WindowsInstaller.Installer
    try {
        if ($installer.ProductState($Contract.productCode) -eq 5) {
            $registered = [string]$installer.ComponentPath($Contract.productCode, $Contract.files[0].componentId)
            if (-not $registered -or [IO.Path]::GetDirectoryName($registered).TrimEnd('\') -ine $Directory.TrimEnd('\')) { throw 'Same-version relocation is not an MSI repair. Uninstall only Power Test before choosing a different application directory; retain user data and shared runtimes.' }
        }
    } finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer) }
}

function Install-Application {
    param([string]$MsiPath, [string]$Directory, [object]$Contract)
    if ($script:Deploy.DryRun) {
        [void](Invoke-DeployProcess 'application' (Join-Path $env:SystemRoot 'System32\msiexec.exe') @('/i', $MsiPath, "INSTALLDIR=$Directory", '/qn', '/norestart', 'REBOOT=ReallySuppress'))
        return
    }
    $actualContract = Get-ApplicationContract $MsiPath $Contract.version
    if ($actualContract.productCode -ne $Contract.productCode) { throw 'MSI ProductCode differs from embedded contract' }
    $installer = New-Object -ComObject WindowsInstaller.Installer
    try {
        $arguments = @('/i', $MsiPath, "INSTALLDIR=$Directory", '/qn', '/norestart', 'REBOOT=ReallySuppress', 'MSIRESTARTMANAGERCONTROL=Disable', '/L*v', (Join-Path $script:Deploy.LogRoot 'application-msi.log'))
        if ($installer.ProductState($Contract.productCode) -eq 5) {
            # MSI repair cannot reliably relocate the same registered product.
            # Refuse before modifying any files rather than pretending to move it.
            $registered = $installer.ComponentPath($Contract.productCode, $Contract.files[0].componentId)
            if ([IO.Path]::GetDirectoryName($registered).TrimEnd('\') -ine $Directory.TrimEnd('\')) { throw 'This version is already installed in another directory. Relocation requires uninstalling the application first; shared drivers and user data must be retained.' }
            $arguments += @('REINSTALL=ALL', 'REINSTALLMODE=vomus')
        }
        [void](Invoke-DeployProcess 'application' (Join-Path $env:SystemRoot 'System32\msiexec.exe') $arguments -TimeoutSeconds 1800)
        foreach ($file in $Contract.files) {
            $expected = Join-Path $Directory $file.name
            $registered = [string]$installer.ComponentPath($Contract.productCode, $file.componentId)
            if (-not $registered -or [IO.Path]::GetFullPath($registered) -ine [IO.Path]::GetFullPath($expected)) { throw "MSI installed $($file.name) at '$registered', NOT the selected directory '$Directory'" }
            if (-not (Test-Path -LiteralPath $expected -PathType Leaf) -or (Get-FileHash -LiteralPath $expected -Algorithm SHA256).Hash -ine $file.sha256) { throw "Installed file missing/hash mismatch: $expected" }
        }
        Write-DeployEvent 'application' 'verified' @{ installDirectory = $Directory; version = $Contract.version }
    } finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer) }
}
