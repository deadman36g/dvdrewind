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
function dvdrewind {
    param(
        [Parameter(Position = 0)]
        [ValidateSet('watch','status','help','web','shell')]
        [string]$Command = 'watch'
    )

    $nas = 'deadman36g@192.168.50.39'
    switch ($Command) {
        'watch' {
            ssh -t $nas 'docker exec -it dvdrewind python populate_all.py --watch'
        }
        'status' {
            ssh $nas 'docker exec dvdrewind python populate_all.py --status'
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
    }
}
# <<< DVDREWIND CLI <<<
'@

$newProfile = $existing.TrimEnd() + "`r`n`r`n" + $block.Trim() + "`r`n"
Set-Content -Path $profilePath -Value $newProfile -Encoding UTF8
. $profilePath

Write-Host ''
Write-Host 'DVDRewind CLI installed.' -ForegroundColor Green
Write-Host 'Run:  dvdrewind          # live read-only monitor' -ForegroundColor Cyan
Write-Host '      dvdrewind status   # one status snapshot'
Write-Host '      dvdrewind help     # population script options'
Write-Host '      dvdrewind web      # open DVDRewind in your browser'
Write-Host '      dvdrewind shell    # enter the NAS container'
