#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\offline\NiPreservation.ps1')
$script:existing = @()
$script:runtimeReady = $false
function Get-ExistingNiSoftware { return $script:existing }
function Test-RuntimeProbe { param($Probe); return $script:runtimeReady }
$manifest = [pscustomobject]@{ components = @([pscustomobject]@{ id = 'ni-runtime'; detect = @([pscustomobject]@{ kind = 'file-version'; minimumVersion = '23.5' }) }) }
if (Assert-NiPreservation $manifest) { throw 'An empty NI baseline cannot count as runtime reuse' }
$script:existing = @([pscustomobject]@{ name = 'NI-DAQmx 9.7.0'; version = '9.70.49152' }, [pscustomobject]@{ name = 'LabVIEW 2011'; version = '11.0' })
$rejected = $false
try { Assert-NiPreservation $manifest | Out-Null } catch { $rejected = $_.Exception.Message -match 'must be preserved' }
if (-not $rejected) { throw 'Legacy NI applications were allowed onto an automatic upgrade path' }
$script:runtimeReady = $true
if (-not (Assert-NiPreservation $manifest)) { throw 'A qualified existing runtime must reuse NI and bypass NIPM installation' }
$manifest.components[0].detect = @()
$rejected = $false
try { Assert-NiPreservation $manifest | Out-Null } catch { $rejected = $true }
if (-not $rejected) { throw 'Missing preservation probes must not imply compatibility' }
Write-Host 'NI preservation preflight passed: legacy changes refused; qualified NI reused without NIPM (mock inventory/probes).'
