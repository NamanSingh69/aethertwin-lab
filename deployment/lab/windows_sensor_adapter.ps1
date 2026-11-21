<#
.SYNOPSIS
  Give one Ethernet adapter a static address on the private sensor network (192.168.1.0/24).

.DESCRIPTION
  Run in an elevated PowerShell (it changes adapter settings). Without -InterfaceAlias it only
  lists adapters. It refuses an adapter that carries a default route (your internet connection),
  sets no gateway and no DNS, and prints the configuration before and after.

  Desktop (processing host): -Address 192.168.1.10   Laptop (operator, optional): -Address 192.168.1.11

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deployment\lab\windows_sensor_adapter.ps1
  powershell -ExecutionPolicy Bypass -File deployment\lab\windows_sensor_adapter.ps1 -InterfaceAlias "Ethernet 3" -Address 192.168.1.10
#>
param([string]$InterfaceAlias,[string]$Address='192.168.1.10',[int]$PrefixLength=24)
$ErrorActionPreference='Stop'

Write-Host 'Physical adapters (Status Up = cable connected):'
Get-NetAdapter -Physical | Sort-Object Name | Format-Table -AutoSize Name,InterfaceDescription,Status,LinkSpeed,MacAddress
if(-not $InterfaceAlias){Write-Host 'Pass -InterfaceAlias "<Name>" with the adapter cabled to the sensor switch.';exit 0}

$principal=[Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run this from an elevated PowerShell (Run as administrator).'}
if($Address -notmatch '^192\.168\.1\.\d{1,3}$' -or $Address -in '192.168.1.2','192.168.1.62'){throw "Use a free address on 192.168.1.0/24 (.2 is the Pi, .62 the L2)."}
$adapter=Get-NetAdapter -Name $InterfaceAlias
if(Get-NetRoute -InterfaceAlias $InterfaceAlias -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue){
  throw "$InterfaceAlias carries a default route (an internet connection). Choose the adapter cabled to the sensor switch."}

Write-Host "Before:";Get-NetIPConfiguration -InterfaceAlias $InterfaceAlias | Format-List InterfaceAlias,IPv4Address,IPv4DefaultGateway,DNSServer
Set-NetIPInterface -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 -Dhcp Disabled
Get-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 -ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false
New-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 -IPAddress $Address -PrefixLength $PrefixLength | Out-Null
Set-DnsClientServerAddress -InterfaceAlias $InterfaceAlias -ResetServerAddresses
Write-Host "After:";Get-NetIPConfiguration -InterfaceAlias $InterfaceAlias | Format-List InterfaceAlias,IPv4Address,IPv4DefaultGateway,DNSServer
Write-Host "Done. $($adapter.InterfaceDescription) is $Address/$PrefixLength with no gateway or DNS. The Windows firewall is unchanged."
