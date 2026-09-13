$ErrorActionPreference = 'Stop'

$profilePath = $PROFILE.CurrentUserCurrentHost
$profileDir = Split-Path -Parent $profilePath
if (-not (Test-Path $profileDir)) {
    New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
}
if (-not (Test-Path $profilePath)) {
    New-Item -ItemType File -Force -Path $profilePath | Out-Null
}

$begin = '# >>> DVDREWIND CLI >>>'
$end = '# <<< DVDREWIND CLI <<<'
$existing = Get-Content -Raw -Path $profilePath
$pattern = '(?ms)^# >>> DVDREWIND CLI >>>.*?^# <<< DVDREWIND CLI <<<\r?\n?'
$existing = [regex]::Replace($existing, $pattern, '')

$block = @'
# >>> DVDREWIND CLI >>>
function Show-DVDRewindNotification {
    param([string]$Message)
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        Add-Type -AssemblyName System.Drawing -ErrorAction Stop
        $notify = New-Object System.Windows.Forms.NotifyIcon
        $notify.Icon = [System.Drawing.SystemIcons]::Information
        $notify.BalloonTipTitle = 'DVD Rewind'
        $notify.BalloonTipText = $Message
        $notify.Visible = $true
        $notify.ShowBalloonTip(10000)
        Start-Sleep -Seconds 5
        $notify.Dispose()
    }
    catch {
        Write-Host "DVD Rewind: $Message" -ForegroundColor Green
    }
}

function Invoke-DVDRewindWatch {
    param(
        [string]$Nas,
        [switch]$NewOnly
    )
    $mode = if ($NewOnly) { '--watch-new' } else { '--watch' }
    ssh -t $Nas "docker exec -it dvdrewind python populate_all.py $mode --exit-on-complete"
    $code = $LASTEXITCODE
    if ($code -eq 20) {
        $summary = ssh $Nas 'docker exec dvdrewind python populate_all.py --completion-message'
        if (-not $summary) { $summary = 'DVD Rewind task finished.' }
        Show-DVDRewindNotification -Message ($summary -join ' ')
    }
    elseif ($code -ne 0) {
        Write-Warning "DVD Rewind monitor exited with code $code"
    }
}

function Invoke-DVDRewindCommandCenter {
    param([string]$Nas)
    $webUrl = 'http://192.168.50.39:8091'
    while ($true) {
        ssh -t $Nas 'docker exec -it -e DVDREWIND_TUI_WRAPPER=1 dvdrewind python populate_all.py --command-center'
        $code = $LASTEXITCODE
        if ($code -eq 20) {
            $summary = ssh $Nas 'docker exec dvdrewind python populate_all.py --completion-message'
            if (-not $summary) { $summary = 'DVD Rewind task finished.' }
            Show-DVDRewindNotification -Message ($summary -join ' ')
            continue
        }
        if ($code -eq 81) {
            Start-Process $webUrl
            continue
        }
        if ($code -eq 82) {
            # F5 requested an in-place CLI code reload. Preserve the terminal
            # session and simply launch the command center process again.
            continue
        }
        if ($code -ne 0) {
            Write-Warning "DVD Rewind Command Center exited with code $code"
        }
        break
    }
}

function dvdrewind {
    param(
        [Parameter(Position = 0)]
        [string]$Command = 'menu',
        [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
        [string[]]$Rest
    )

    $nas = 'deadman36g@192.168.50.39'
    switch ($Command.ToLowerInvariant()) {
        'menu' {
            Invoke-DVDRewindCommandCenter -Nas $nas
        }
        'watch' {
            Invoke-DVDRewindWatch -Nas $nas
        }
        'new' {
            Invoke-DVDRewindWatch -Nas $nas -NewOnly
        }
        'status' {
            ssh $nas 'docker exec dvdrewind python populate_all.py --status'
        }
        'search' {
            $query = ($Rest -join ' ').Trim()
            if (-not $query) {
                $query = Read-Host 'Search DVDRewind'
            }
            $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($query))
            ssh -t $nas "docker exec -it dvdrewind python populate_all.py --search-b64 $encoded"
        }
        'retry' {
            ssh -t $nas 'docker exec -it dvdrewind python populate_all.py --retry-failed'
        }
        'help' {
            ssh -t $nas 'docker exec -it dvdrewind python populate_all.py --help'
        }
        'web' {
            Start-Process 'http://192.168.50.39:8091'
        }
        'shell' {
            ssh -t $nas 'docker exec -it dvdrewind bash'
        }
        default {
            Write-Host 'DVDRewind commands:' -ForegroundColor Cyan
            Write-Host '  dvdrewind                  Unified interactive Command Center'
            Write-Host '  dvdrewind menu             Same Command Center explicitly'
            Write-Host '  dvdrewind new              Watch only new discoveries'
            Write-Host '  dvdrewind status           One status snapshot'
            Write-Host '  dvdrewind search "Title"  Search the archive'
            Write-Host '  dvdrewind retry            Retry saved failed FIDs'
            Write-Host '  dvdrewind web              Open the web UI'
            Write-Host '  dvdrewind shell            Enter the NAS container'
            Write-Host '  dvdrewind help             Show Python CLI options'
        }
    }
}
# <<< DVDREWIND CLI <<<
'@

$newProfile = $existing.TrimEnd() + "`r`n`r`n" + $block.Trim() + "`r`n"
Set-Content -Path $profilePath -Value $newProfile -Encoding UTF8
. $profilePath

Write-Host ''
Write-Host 'DVDRewind CLI installed/upgraded.' -ForegroundColor Green
Write-Host 'Run:  dvdrewind                  # unified interactive Command Center' -ForegroundColor Cyan
Write-Host '      dvdrewind new              # only new discoveries'
Write-Host '      dvdrewind search "The Thing"'
Write-Host '      dvdrewind retry            # retry previous failures'
Write-Host '      dvdrewind status           # one status snapshot'
Write-Host '      dvdrewind web              # open DVDRewind'
Write-Host ''
Write-Host 'Main footer: N Best Next, F Find, H Keys, F5 Reload, Q Quit. R refreshes data without restarting.' -ForegroundColor DarkGray
