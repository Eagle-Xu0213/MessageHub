param(
    [ValidateSet('install', 'uninstall')]
    [string]$Action = 'install',
    [string]$DshCli
)

$ErrorActionPreference = 'Stop'
if (-not $DshCli) {
    $handler = Get-Item -LiteralPath 'Registry::HKEY_CLASSES_ROOT\dsh\shell\open\command' -ErrorAction SilentlyContinue
    if ($handler -and $handler.GetValue('') -match '^"([^"]+)"') {
        $DshCli = Join-Path (Split-Path -Parent $Matches[1]) 'resources\runtime\cli\bin\dsh.cmd'
    }
}
if (-not $DshCli -or -not (Test-Path -LiteralPath $DshCli -PathType Leaf)) {
    throw 'DeepSeek Harness CLI not found. Pass its dsh.cmd path with -DshCli.'
}

if ($Action -eq 'install') {
    $version = & $DshCli --version
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^0\.2\.3(?:[.-]|$)') {
        throw 'This integration targets DeepSeek Harness 0.2.3 desktop.'
    }
    $plugin = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\integrations\dsh')).Path.Replace('\', '/')
    & $DshCli plugin --profile desktop add "link:$plugin" --offline
} else {
    & $DshCli plugin --profile desktop remove messagehub-dsh
}
if ($LASTEXITCODE -ne 0) {
    throw "DeepSeek Harness plugin command failed ($LASTEXITCODE)."
}

Write-Output 'Restart DeepSeek Harness to apply the change.'
