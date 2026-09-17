#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\offline\Common.ps1')
. (Join-Path $PSScriptRoot '..\offline\Application.ps1')

# Exercise the real Windows Installer COM boundary without installing anything.
# Its void methods emit null values unless explicitly suppressed.
$path = Join-Path ([IO.Path]::GetTempPath()) ('arp-msi-contract-' + [guid]::NewGuid().ToString('N') + '.msi')
$installer = New-Object -ComObject WindowsInstaller.Installer
$db = $null
function Invoke-TestMsiSql {
    param([string]$Sql)
    $view = $db.OpenView($Sql)
    try { [void]$view.Execute() }
    finally { [void]$view.Close(); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($view) }
}
try {
    $db = $installer.OpenDatabase($path, 3)
    Invoke-TestMsiSql 'CREATE TABLE `Property` (`Property` CHAR(72) NOT NULL, `Value` CHAR(255) PRIMARY KEY `Property`)'
    Invoke-TestMsiSql 'CREATE TABLE `Directory` (`Directory` CHAR(72) NOT NULL PRIMARY KEY `Directory`)'
    Invoke-TestMsiSql 'CREATE TABLE `Component` (`Component` CHAR(72) NOT NULL, `ComponentId` CHAR(38), `Directory_` CHAR(72), `KeyPath` CHAR(72) PRIMARY KEY `Component`)'
    Invoke-TestMsiSql 'CREATE TABLE `File` (`File` CHAR(72) NOT NULL, `Component_` CHAR(72), `FileName` CHAR(255) PRIMARY KEY `File`)'
    Invoke-TestMsiSql "INSERT INTO ``Property`` (``Property``, ``Value``) VALUES ('ProductVersion', '0.1.2')"
    Invoke-TestMsiSql "INSERT INTO ``Property`` (``Property``, ``Value``) VALUES ('ProductCode', '{D1B43239-E193-4850-933F-42D578B59EE0}')"
    Invoke-TestMsiSql "INSERT INTO ``Property`` (``Property``, ``Value``) VALUES ('UpgradeCode', '{A75494C7-DA4E-49C9-8422-16B53378927A}')"
    Invoke-TestMsiSql "INSERT INTO ``Directory`` (``Directory``) VALUES ('INSTALLDIR')"
    Invoke-TestMsiSql "INSERT INTO ``Component`` (``Component``, ``ComponentId``, ``Directory_``, ``KeyPath``) VALUES ('main', '{B942640A-60CD-4522-91CB-C2728D655EE2}', 'INSTALLDIR', 'mainfile')"
    Invoke-TestMsiSql "INSERT INTO ``Component`` (``Component``, ``ComponentId``, ``Directory_``, ``KeyPath``) VALUES ('sidecar', '{97C39F7F-AD17-4375-B777-DAB900DA7AF8}', 'INSTALLDIR', 'sidecarfile')"
    Invoke-TestMsiSql "INSERT INTO ``File`` (``File``, ``Component_``, ``FileName``) VALUES ('mainfile', 'main', 'tauri-ui.exe')"
    Invoke-TestMsiSql "INSERT INTO ``File`` (``File``, ``Component_``, ``FileName``) VALUES ('sidecarfile', 'sidecar', 'arp-python.exe')"
    [void]$db.Commit()
    $rows = @(Read-MsiRows $db 'SELECT `Property`, `Value` FROM `Property`' 2)
    if ($rows.Count -ne 3 -or @($rows | Where-Object { $null -eq $_ }).Count) { throw 'MSI rows contain COM void output' }
    $empty = @(Read-MsiRows $db 'SELECT `Property`, `Value` FROM `Property` WHERE `Property` = ''missing''' 2)
    if ($empty.Count -ne 0) { throw 'Empty MSI query must return no pipeline objects' }
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($db)
    $db = $null
    $contract = Get-ApplicationContract $path '0.1.2'
    if ($contract.files.Count -ne 2) { throw 'Valid MSI contract rejected' }
    $rejected = $false
    try { [void](Get-ApplicationContract $path '0.1.3') } catch { $rejected = $true }
    if (-not $rejected) { throw 'Wrong MSI version accepted' }
    $db = $installer.OpenDatabase($path, 1)
    Invoke-TestMsiSql "UPDATE ``Component`` SET ``Directory_`` = 'OTHERDIR' WHERE ``Component`` = 'sidecar'"
    [void]$db.Commit()
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($db)
    $db = $null
    $rejected = $false
    try { [void](Get-ApplicationContract $path '0.1.2') } catch { $rejected = $true }
    if (-not $rejected) { throw 'Sidecar outside INSTALLDIR accepted' }
} finally {
    if ($db) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($db) }
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer)
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
}
Write-Host 'Real MSI COM row and directory-contract regression checks passed; no product installed.'
