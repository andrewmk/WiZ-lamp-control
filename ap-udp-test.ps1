# ap-udp-test.ps1 — host-side control test for the WiZ AP pairing window.
#
# Run from the Windows host (PowerShell) while connected to the bulb's
# WiZConfig_* AP, in the SAME window as `pair.py diag` in the WSL2 VM.
#
# Interpretation:
#   - host gets UDP replies, VM doesn't  -> WSL2 NAT/firewall is the
#     blocker (see README "Next steps": mirrored networking, firewall
#     rule, or run pair.py send on the host / a Linux laptop).
#   - neither gets replies               -> the bulb's AP mode doesn't
#     serve these ports; the protocol assumption needs revisiting.
#
# Usage (from the host):
#   powershell -ExecutionPolicy Bypass -File .\ap-udp-test.ps1
# or point it at a copy of this file inside WSL:
#   powershell -ExecutionPolicy Bypass -File \\wsl$\<distro>\home\sport\wiz-lamps\ap-udp-test.ps1

$ip = "192.168.56.1"
$ports = @(38899, 38900, 3889, 8909, 56700, 4444, 80, 55443)
$msg = [System.Text.Encoding]::UTF8.GetBytes('{"method":"getSystemConfig","params":{},"id":1}')

Write-Host "Probing $ip on ports: $($ports -join ', ')"
$hits = @()
foreach ($port in $ports) {
    $c = New-Object System.Net.Sockets.UdpClient
    $c.Client.ReceiveTimeout = 1500
    $c.Send($msg, $msg.Length, $ip, $port) | Out-Null
    try {
        $ep = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
        $r = $c.Receive([ref]$ep)
        $text = [System.Text.Encoding]::UTF8.GetString($r)
        Write-Host "RESPONSE $ip`:$port <- $text"
        $hits += $port
    } catch {
        Write-Host "no reply   $ip`:$port"
    } finally {
        $c.Close()
    }
}

if ($hits.Count -gt 0) {
    Write-Host "`nreachable ports: $($hits -join ', ')"
} else {
    Write-Host "`nno UDP replies from $ip on any candidate port."
}
