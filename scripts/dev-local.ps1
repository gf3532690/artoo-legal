<#
.SYNOPSIS
本地开发启动：中间件用 docker，**应用跑在宿主机**（uvicorn --reload / vite dev），不构建镜像。

.DESCRIPTION
等价于仓库 Makefile 的 `make infra` + `make dev`，只是本机没有 make，且 Windows 下路径与
环境变量写法不同。做三件事：

  1. 停掉**镜像版**的应用容器（backend / worker / frontend）——否则 8000 端口被它们占着；
  2. 起中间件（`docker compose --profile infra up -d --wait`）。docker-compose.override.yml
     会被 compose 自动叠加，把 PG / Redis / Milvus / MinIO 的端口暴露到宿主机；
  3. 在宿主机起三个进程，改代码即时生效：

       uvicorn app.main:app --reload --port 8000   （后端）
       python -m app.worker_main                   （Pipeline Worker，入库用）
       npm run dev                                 （前端 Vite，HMR）

  环境与容器**同源**：工作目录设成仓库根，让 pydantic 读到根目录的 `.env`（`model_config`
  里的 `env_file = ".env"` 是按工作目录找的），再把 DATABASE_URL / MILVUS_HOST / REDIS_URL /
  MINIO_ENDPOINT 四个换成 localhost ——也就是 compose 给容器注入的那几个。

  因此不需要额外维护 `backend/.env`：容器与宿主机进程共用同一份根 `.env`（含那两把法条库
  Key 与 Embedding/Rerank 地址）。

.PARAMETER Python
用哪个 Python 解释器。缺省依次尝试 `$env:DEV_PYTHON` → `<repo>\.venv\Scripts\python.exe` →
PATH 上的 `python`，并要求它能 import 后端依赖（uvicorn / fastapi / pymilvus）。

.EXAMPLE
  .\scripts\dev-local.ps1
  .\scripts\dev-local.ps1 -Python C:\Users\me\.conda\envs\aladdin\python.exe
  .\scripts\dev-local.ps1 -SkipInfra      # 中间件已经在跑
  .\scripts\dev-local.ps1 -NoWorker       # 只想调检索，不跑入库 Worker
  .\scripts\dev-local.ps1 -Stop           # 停掉这三个进程（中间件容器不动）
#>

param(
    [string]$Python = $env:DEV_PYTHON,
    [switch]$Stop,
    [switch]$SkipInfra,
    [switch]$NoWorker
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

# ---------------------------------------------------------------
# 进程台账：只记三个应用进程的 PID，用来 -Stop
# ---------------------------------------------------------------

function Get-DevPid([string]$name) {
    $file = Join-Path $logs "$name.pid"
    if (-not (Test-Path -LiteralPath $file)) { return $null }
    $text = (Get-Content -LiteralPath $file -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $text) { return $null }
    return [int]$text
}

function Set-DevPid([string]$name, [int]$procId) {
    Set-Content -LiteralPath (Join-Path $logs "$name.pid") -Value $procId -Encoding ascii
}

function Stop-Tree([int]$rootPid) {
    # 连子进程一起收：uvicorn --reload 与 vite 都会派生（父进程只负责看文件、子进程才监听），
    # 只杀父进程会留下监听端口的孤儿，只杀子进程父进程又会把它拉回来。
    #
    # 刻意**不按端口杀**：这台机器上还可能跑着另一个 checkout（如 aladdin）的同名开发进程，
    # 按端口收会误伤。这里只顺着我们自己记录的 PID 往下找。
    $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Select-Object ProcessId, ParentProcessId
    $targets = @($rootPid)
    $grew = $true
    while ($grew) {
        $grew = $false
        foreach ($proc in $all) {
            if (($targets -contains $proc.ParentProcessId) -and ($targets -notcontains $proc.ProcessId)) {
                $targets += $proc.ProcessId
                $grew = $true
            }
        }
    }
    # 先子后父
    foreach ($procId in ($targets | Sort-Object -Descending)) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}

function Stop-DevApp {
    foreach ($name in @("dev-backend", "dev-worker", "dev-frontend")) {
        $procId = Get-DevPid $name
        if ($procId) { Stop-Tree $procId }
        Remove-Item -LiteralPath (Join-Path $logs "$name.pid") -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 600
    foreach ($port in @(8000, 3000)) {
        $owner = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty OwningProcess
        if ($owner) {
            Write-Host "  提示：端口 $port 仍被 pid $owner 占用（不是本脚本起的进程？）" -ForegroundColor Yellow
        }
    }
}

function Start-Logged([string]$name, [string]$file, [string[]]$arguments, [string]$workDir) {
    if (-not $workDir) { $workDir = $root }
    $out = Join-Path $logs "$name.log"
    $err = Join-Path $logs "$name.err.log"
    Write-Host "  → $name  ($($arguments -join ' '))  [cwd=$workDir]"
    $proc = Start-Process -FilePath $file -ArgumentList $arguments `
        -WorkingDirectory $workDir -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    Set-DevPid $name $proc.Id
    return $proc
}

# ---------------------------------------------------------------
# -Stop
# ---------------------------------------------------------------

if ($Stop) {
    Write-Host "停掉宿主机上的后端 / Worker / 前端…" -ForegroundColor Cyan
    Stop-DevApp
    Write-Host "已停。中间件容器未动；要停它们：docker compose --profile infra down" -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------
# Python 解释器：能 import 后端依赖才算可用
# ---------------------------------------------------------------

function Test-Python([string]$candidate) {
    if (-not $candidate) { return $false }
    try {
        & $candidate -c "import uvicorn, fastapi, pymilvus" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

$candidates = @()
if ($Python) { $candidates += $Python }
$candidates += (Join-Path $root ".venv\Scripts\python.exe")
$candidates += "python"

$pythonExe = $null
foreach ($candidate in $candidates) {
    if (Test-Python $candidate) { $pythonExe = $candidate; break }
}

if (-not $pythonExe) {
    Write-Host "找不到可用的 Python 解释器（试过：$($candidates -join '、')）。" -ForegroundColor Red
    Write-Host ""
    Write-Host "两种做法：" -ForegroundColor Yellow
    Write-Host "  1) 用现成的环境指定： .\scripts\dev-local.ps1 -Python <你的python.exe>"
    Write-Host "     （或设环境变量 DEV_PYTHON）"
    Write-Host "  2) 在仓库根建一个（等价于 make install-backend，首次要下载依赖）："
    Write-Host "     python -m venv .venv"
    Write-Host "     .venv\Scripts\pip install -r backend\requirements.txt"
    exit 1
}

Write-Host "Python: $pythonExe" -ForegroundColor DarkGray

# ---------------------------------------------------------------
# 环境：与容器同源的根 .env，只把四个连接目标换成 localhost
# ---------------------------------------------------------------

$pgPassword = "postgres"
$envFile = Join-Path $root ".env"
if (Test-Path -LiteralPath $envFile) {
    $line = Get-Content -LiteralPath $envFile |
        Where-Object { $_ -match '^\s*POSTGRES_PASSWORD\s*=' } |
        Select-Object -First 1
    if ($line) { $pgPassword = ($line -split '=', 2)[1].Trim() }
}

$env:DATABASE_URL = "postgresql+asyncpg://postgres:$pgPassword@localhost:5432/artoo"
$env:MILVUS_HOST = "localhost"
$env:MILVUS_PORT = "19530"
$env:REDIS_URL = "redis://localhost:6379/0"
$env:MINIO_ENDPOINT = "localhost:9000"
$env:PYTHONPATH = Join-Path $root "backend"   # 让 `app.*` 在仓库根也能 import
$env:PYTHONDONTWRITEBYTECODE = "1"

# ---------------------------------------------------------------
# 1) 腾出端口 + 2) 起中间件
# ---------------------------------------------------------------

Write-Host "停掉镜像版应用容器（若在跑）…" -ForegroundColor Cyan
Push-Location $root
try {
    # 两个 profile 都要给：只开 app 时 infra 服务不在活动项目里，worker 的
    # `depends_on: minio` 会被判成「依赖未定义的服务」而直接报错。
    docker compose --profile infra --profile app stop backend worker frontend 2>&1 | Out-Null
    Stop-DevApp   # 顺手收掉上一次由本脚本起的进程
} catch {
    Write-Host "  （跳过：$($_.Exception.Message.Trim())）" -ForegroundColor DarkGray
} finally {
    Pop-Location
}

if (-not $SkipInfra) {
    Write-Host "启动中间件（等待健康检查）…" -ForegroundColor Cyan
    Push-Location $root
    try {
        docker compose --profile infra up -d --wait
        if ($LASTEXITCODE -ne 0) { throw "中间件没起来，先看 docker compose logs" }
    } catch {
        Write-Host "中间件启动失败：$($_.Exception.Message)" -ForegroundColor Red
        exit 1
    } finally {
        Pop-Location
    }
} else {
    Write-Host "跳过中间件（-SkipInfra）" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------
# 3) 三个应用进程
# ---------------------------------------------------------------

Write-Host "启动应用（宿主机，改代码即时生效）…" -ForegroundColor Cyan
# 后端 / Worker：工作目录必须是**仓库根**，pydantic 的 `env_file = ".env"` 是按工作目录找的，
# 这样才与容器读到同一份根 .env（app.* 由 PYTHONPATH 指到 backend）。
Start-Logged "dev-backend" $pythonExe @(
    "-m", "uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", "8000"
) | Out-Null

if (-not $NoWorker) {
    Start-Logged "dev-worker" $pythonExe @("-m", "app.worker_main") | Out-Null
}

$npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $npm) { $npm = "npm" }
# 前端反过来：npm 必须在 frontend/ 下跑（package.json 在那儿），与根 .env 无关。
Start-Logged "dev-frontend" $npm @("run", "dev") (Join-Path $root "frontend") | Out-Null

Write-Host ""
Write-Host " 后端  http://localhost:8000      （uvicorn --reload，日志 logs\dev-backend.log）"
Write-Host " 前端  http://localhost:3000      （vite HMR，日志 logs\dev-frontend.log）"
if (-not $NoWorker) {
    Write-Host " Worker                          （入库用，日志 logs\dev-worker.log）"
}
Write-Host ""
Write-Host " 停止： .\scripts\dev-local.ps1 -Stop" -ForegroundColor Green
Write-Host " 中间件：docker compose --profile infra down" -ForegroundColor DarkGray
Write-Host ""
Write-Host " 首次起来后端要建表/种数据，约十几秒；没就绪就看 logs\dev-backend.err.log。" -ForegroundColor DarkGray
