<#
.SYNOPSIS
  Let the operator laptop drive this desktop over SSH: key-only, reachable only from the sensor network.

.DESCRIPTION
  Run once on the A2000 desktop in an elevated PowerShell. It installs the Windows OpenSSH Server
  feature if needed, starts sshd automatically, allows ONLY public-key login (password login off),
  authorises the one public key you pass, and limits the inbound firewall rule to the sensor subnet
  (default 192.168.1.0/24; add your Tailscale range with -AllowFrom if you want it there too).
  It prints what it changed. Undo: Stop-Service sshd; Set-Service sshd -StartupType Disabled.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deployment\lab\windows_enable_ssh.ps1 -PublicKeyFile .\aethertwin_ed25519.pub
#>
param([Parameter(Mandatory)][string]$PublicKeyFile,[string[]]$AllowFrom=@('192.168.1.0/24'))
$ErrorActionPreference='Stop'
$principal=[Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run this from an elevated PowerShell (Run as administrator).'}
$key=(Get-Content -Raw $PublicKeyFile).Trim()
if($key -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+( .*)?$'){throw 'Expected one ssh-ed25519 public key (the .pub file, never the private key).'}

$capability=Get-WindowsCapability -Online -Name 'OpenSSH.Server*'
if($capability.State -ne 'Installed'){Write-Host "Installing $($capability.Name)";Add-WindowsCapability -Online -Name $capability.Name | Out-Null}
Set-Service sshd -StartupType Automatic;Start-Service sshd

# Key-only login. Administrators' keys live in administrators_authorized_keys (Windows OpenSSH rule).
$config="$env:ProgramData\ssh\sshd_config";$text=Get-Content -Raw $config
foreach($setting in 'PasswordAuthentication no','PubkeyAuthentication yes'){
  $name=$setting.Split(' ')[0]
  if($text -match "(?m)^\s*#?\s*$name\s+\S+"){$text=[regex]::Replace($text,"(?m)^\s*#?\s*$name\s+\S+",$setting)}else{$text=$setting+"`r`n"+$text}
}
Set-Content -Path $config -Value $text -Encoding ascii
# This elevated account is an administrator, and Windows OpenSSH reads administrators' keys only from here.
$keys="$env:ProgramData\ssh\administrators_authorized_keys"
$existing=if(Test-Path $keys){Get-Content $keys}else{@()}
if($existing -notcontains $key){Add-Content -Path $keys -Value $key -Encoding ascii}
icacls $keys /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null
Restart-Service sshd

$rule=Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
if(-not $rule){$rule=New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow}
# The unidentified sensor network is a Public profile; the remote-address limit is what keeps this narrow.
Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Profile Any -RemoteAddress $AllowFrom -Enabled True
Write-Host "sshd: $((Get-Service sshd).Status), key-only, key file $keys"
Write-Host "Firewall: port 22 inbound only from $($AllowFrom -join ', ')"
Write-Host "Login name for the laptop: $env:USERNAME"
