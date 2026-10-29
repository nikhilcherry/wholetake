# Cross-platform deployment lifecycle script for WholeTake + Roomstamp (Windows PowerShell)
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$DefaultRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$RootDir = if ($env:WT_DEPLOY_ROOT) { $env:WT_DEPLOY_ROOT } else { $DefaultRoot }

$DryRun = $false
$WithDaemons = $false
$EnvFile = ""
$Subcommand = ""
$LocalAction = "start"

function Show-Usage {
    Write-Output @"
Usage: .\deploy\deploy.ps1 <subcommand> [options]

Subcommands:
  build                  Build Docker Compose images
  up | start             Ensure runtime dirs/keys and start Docker Compose stack
  down | stop            Stop Docker Compose stack
  status                 Show Docker Compose and local-demo status
  logs                   Tail service logs
  check-env              Initialize runtime dirs/ephemeral venue key and check config
  local-demo [action]    Manage native local Python processes (start|stop|status)

Options:
  --dry-run, -DryRun              Print commands and initialize dirs/keys without running Docker
  --with-daemons, -WithDaemons    Enable optional background daemons (--profile daemons)
  --env-file, -EnvFile <path>     Specify custom .env file path
  --root, -Root <path>            Override working root directory (default: repo root)
  -h, --help                      Show this help message
"@
}

$i = 0
while ($i -lt $args.Count) {
    $arg = [string]$args[$i]
    switch -Regex ($arg) {
        '^(build|up|start|down|stop|status|logs|check-env)$' {
            if (-not $Subcommand) {
                $Subcommand = $arg
            }
            $i++
            break
        }
        '^local-demo$' {
            $Subcommand = "local-demo"
            $i++
            if ($i -lt $args.Count -and -not ([string]$args[$i]).StartsWith("-")) {
                $LocalAction = [string]$args[$i]
                $i++
            }
            break
        }
        '^(--dry-run|-DryRun)$' {
            $DryRun = $true
            $i++
            break
        }
        '^(--with-daemons|-WithDaemons)$' {
            $WithDaemons = $true
            $i++
            break
        }
        '^(--env-file|-EnvFile)$' {
            $i++
            if ($i -ge $args.Count) {
                Write-Error "Missing value for $arg"
                exit 1
            }
            $EnvFile = [string]$args[$i]
            $i++
            break
        }
        '^(--root|-Root)$' {
            $i++
            if ($i -ge $args.Count) {
                Write-Error "Missing value for $arg"
                exit 1
            }
            $RootDir = [string]$args[$i]
            $i++
            break
        }
        '^(-h|--help|-Help)$' {
            Show-Usage
            exit 0
        }
        default {
            Write-Error "Unknown argument: $arg"
            Show-Usage
            exit 1
        }
    }
}

if (-not $Subcommand) {
    Show-Usage
    exit 1
}

function New-Hex32Key {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    return ([System.BitConverter]::ToString($bytes)).Replace("-", "").ToLowerInvariant()
}

function Initialize-RuntimeEnv {
    $dirs = @(
        (Join-Path $RootDir "data/witness"),
        (Join-Path $RootDir "data/verdicts"),
        (Join-Path $RootDir "data/simx"),
        (Join-Path $RootDir "logs"),
        (Join-Path $RootDir "keys")
    )
    foreach ($d in $dirs) {
        if (-not (Test-Path -LiteralPath $d)) {
            New-Item -ItemType Directory -Path $d -Force | Out-Null
        }
    }

    $keyFile = Join-Path $RootDir "keys/venue.key"
    $needKey = $true
    if (Test-Path -LiteralPath $keyFile) {
        $existing = (Get-Content -LiteralPath $keyFile -Raw -ErrorAction SilentlyContinue)
        if ($existing -and $existing.Trim().Length -ge 64) {
            $needKey = $false
        }
    }

    if ($needKey) {
        $hexKey = New-Hex32Key
        [System.IO.File]::WriteAllText($keyFile, "$hexKey`n", [System.Text.Encoding]::ASCII)
        Write-Output "[deploy] Generated ephemeral venue key at $keyFile (mode 0600)"
    }
}

$ComposeArgs = @()
$rootCompose = Join-Path $RootDir "docker-compose.yml"
$defaultCompose = Join-Path $DefaultRoot "docker-compose.yml"
if (Test-Path -LiteralPath $rootCompose) {
    $ComposeArgs += @("-f", $rootCompose)
} elseif (Test-Path -LiteralPath $defaultCompose) {
    $ComposeArgs += @("-f", $defaultCompose)
}

if ($EnvFile) {
    $ComposeArgs += @("--env-file", $EnvFile)
} elseif (Test-Path -LiteralPath (Join-Path $RootDir ".env")) {
    $ComposeArgs += @("--env-file", (Join-Path $RootDir ".env"))
}

if ($WithDaemons) {
    $ComposeArgs += @("--profile", "daemons")
}

function Invoke-Compose {
    param([string[]]$SubArgs)
    $allArgs = $ComposeArgs + $SubArgs
    $cmdStr = "docker compose " + ($allArgs -join " ")
    if ($DryRun) {
        Write-Output "[dry-run] $cmdStr"
        return
    }
    Write-Output "[deploy] Running: $cmdStr"
    & docker compose @allArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Invoke-LocalDemo {
    param([string]$Action)
    Initialize-RuntimeEnv
    $simxPidFile = Join-Path $RootDir "logs/simx.pid"
    $verdictPidFile = Join-Path $RootDir "logs/verdict.pid"
    $botPidFile = Join-Path $RootDir "logs/bot.pid"
    $simxData = Join-Path $RootDir "data/simx"
    $witnessRoot = Join-Path $RootDir "data/witness"
    $verdictsDir = Join-Path $RootDir "data/verdicts"

    switch -Regex ($Action) {
        '^(start|up)$' {
            $simxCmd = "python -m simx serve --host 127.0.0.1 --port 8791 --data $simxData"
            $verdictCmd = "python -m verdict serve --host 127.0.0.1 --port 8790"
            if ($DryRun) {
                Write-Output "[dry-run] $simxCmd"
                Write-Output "[dry-run] WT_FETCH_ALLOW_HOSTS=127.0.0.1:8791 WT_WITNESS_ROOT=$witnessRoot WT_VERDICTS_DIR=$verdictsDir WHOLETAKE_ASR=none $verdictCmd"
                if ($WithDaemons) {
                    Write-Output "[dry-run] X_API_BASE=http://127.0.0.1:8791 X_BOT_TOKEN=sim-wholetake WT_VERDICT_URL=http://127.0.0.1:8790 WT_PAGE_BASE=http://127.0.0.1:8790 python -m bot run"
                }
                return
            }

            $pSimx = Start-Process -FilePath "python" -ArgumentList @("-m", "simx", "serve", "--host", "127.0.0.1", "--port", "8791", "--data", $simxData) -WorkingDirectory $DefaultRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $RootDir "logs/simx.log") -RedirectStandardError (Join-Path $RootDir "logs/simx.err.log")
            [System.IO.File]::WriteAllText($simxPidFile, "$($pSimx.Id)")

            $env:WT_FETCH_ALLOW_HOSTS = "127.0.0.1:8791"
            $env:WT_WITNESS_ROOT = $witnessRoot
            $env:WT_VERDICTS_DIR = $verdictsDir
            if (-not $env:WHOLETAKE_ASR) { $env:WHOLETAKE_ASR = "none" }

            $pVerdict = Start-Process -FilePath "python" -ArgumentList @("-m", "verdict", "serve", "--host", "127.0.0.1", "--port", "8790") -WorkingDirectory $DefaultRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $RootDir "logs/verdict.log") -RedirectStandardError (Join-Path $RootDir "logs/verdict.err.log")
            [System.IO.File]::WriteAllText($verdictPidFile, "$($pVerdict.Id)")

            if ($WithDaemons) {
                $env:X_API_BASE = "http://127.0.0.1:8791"
                $env:X_BOT_TOKEN = "sim-wholetake"
                $env:WT_VERDICT_URL = "http://127.0.0.1:8790"
                $env:WT_PAGE_BASE = "http://127.0.0.1:8790"
                $env:BOT_POLL_S = "5"
                $env:BOT_INCLUDE_LINK = "1"
                $pBot = Start-Process -FilePath "python" -ArgumentList @("-m", "bot", "run") -WorkingDirectory $DefaultRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $RootDir "logs/bot.log") -RedirectStandardError (Join-Path $RootDir "logs/bot.err.log")
                [System.IO.File]::WriteAllText($botPidFile, "$($pBot.Id)")
            }

            Write-Output "[deploy] Started local-demo processes (verdict :8790, simx :8791)"
            Write-Output "[deploy] Windows 11 WASAPI live emission hint: python -m roomstamp emit --mode table --hostapi WASAPI --out-device 'Speakers (Realtek' --key-file keys/venue.key --log-dir logs"
            break
        }
        '^(stop|down)$' {
            if ($DryRun) {
                Write-Output "[dry-run] Stop PIDs in $simxPidFile $verdictPidFile $botPidFile"
                return
            }
            foreach ($pf in @($botPidFile, $verdictPidFile, $simxPidFile)) {
                if (Test-Path -LiteralPath $pf) {
                    $rawPid = (Get-Content -LiteralPath $pf -Raw -ErrorAction SilentlyContinue).Trim()
                    if ($rawPid) {
                        Stop-Process -Id ([int]$rawPid) -Force -ErrorAction SilentlyContinue
                    }
                    Remove-Item -LiteralPath $pf -Force -ErrorAction SilentlyContinue
                }
            }
            Write-Output "[deploy] Stopped local-demo processes"
            break
        }
        '^status$' {
            foreach ($name in @("simx", "verdict", "bot")) {
                $pf = Join-Path $RootDir "logs/$name.pid"
                if (Test-Path -LiteralPath $pf) {
                    $rawPid = (Get-Content -LiteralPath $pf -Raw -ErrorAction SilentlyContinue).Trim()
                    Write-Output "local-demo $name pid=$rawPid"
                } else {
                    Write-Output "local-demo $name stopped"
                }
            }
            break
        }
        default {
            Write-Error "Invalid local-demo action: $Action (expected start|stop|status)"
            exit 1
        }
    }
}

switch -Regex ($Subcommand) {
    '^check-env$' {
        Initialize-RuntimeEnv
        Write-Output "[check-env] ROOT_DIR=$RootDir"
        Write-Output "[check-env] VENUE_KEY=$(Join-Path $RootDir 'keys/venue.key')"
        Write-Output "[check-env] DIRECTORIES=data/witness data/verdicts data/simx logs keys"
        Write-Output "[check-env] OK"
        break
    }
    '^build$' {
        Initialize-RuntimeEnv
        Invoke-Compose @("build")
        break
    }
    '^(up|start)$' {
        Initialize-RuntimeEnv
        Invoke-Compose @("up", "-d")
        break
    }
    '^(down|stop)$' {
        Invoke-Compose @("down")
        break
    }
    '^status$' {
        Invoke-Compose @("ps")
        break
    }
    '^logs$' {
        Invoke-Compose @("logs", "-f")
        break
    }
    '^local-demo$' {
        Invoke-LocalDemo -Action $LocalAction
        break
    }
    default {
        Write-Error "Unknown subcommand: $Subcommand"
        Show-Usage
        exit 1
    }
}
