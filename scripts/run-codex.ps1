param(
    [Parameter(Mandatory = $true)]
    [string]$Prompt
)

$ErrorActionPreference = "Stop"
$collector = "http://127.0.0.1:38427/v1/ingest"

python "$PSScriptRoot\hudctl.py" ensure | Out-Null

codex exec --json $Prompt | ForEach-Object {
    $line = [string]$_
    Write-Output $line
    if ($line.TrimStart().StartsWith("{")) {
        try {
            Invoke-RestMethod -Uri $collector -Method Post -ContentType "application/json" -Headers @{ "X-Codex-Hud-Source" = "codex-jsonl-wrapper" } -Body $line | Out-Null
        }
        catch {
            Write-Warning "发送 usage 到 HUD 失败：$($_.Exception.Message)"
        }
    }
}
