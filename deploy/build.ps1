# ============================================================
# Artoo 离线部署包构建（Windows / PowerShell，在有网的开发机执行）
#
# 产物 dist\ 目录可整体拷到内网服务器，再执行 install.sh 部署。
#
# 用法：
#   .\deploy\build.ps1                  # 当前架构，含中间件镜像（首次部署），仅产出 dist\
#   .\deploy\build.ps1 -Arch arm64      # 指定目标架构（amd64 | arm64）
#   .\deploy\build.ps1 -AppOnly         # 只打应用镜像（迭代更新，不含中间件）
#   .\deploy\build.ps1 -WithGraph       # 额外安装知识图谱依赖（Neo4j 驱动）并导出 Neo4j 镜像
#   .\deploy\build.ps1 -Tar             # 打包完自动压缩为 artoo-deploy.tar.gz
#
# 默认只产出 dist\，不压缩——方便先改 dist\ 里的配置（如 .env.example、
# docker-compose.yml）再手动压缩。需要直接出压缩包时加 -Tar。
# ============================================================
param(
    [string]$Arch = "",        # 留空 = 跟随本机架构
    [switch]$AppOnly,          # 仅应用镜像
    [switch]$WithGraph,        # 额外安装知识图谱依赖（Neo4j 驱动）并导出 Neo4j 镜像
    [switch]$Tar               # 打包完自动压缩为 artoo-deploy.tar.gz
)

$ErrorActionPreference = "Stop"

# 切到项目根目录（脚本位于 deploy\ 下）
Set-Location (Join-Path $PSScriptRoot "..")

$platformArgs = @()
$savePlatformArgs = @()
if ($Arch) {
    $platformArgs = @("--platform", "linux/$Arch")
    # 跨架构导出必须指定平台：containerd 镜像存储下不指定会报
    # "no suitable export target found ... does not provide the specified platform"。
    $savePlatformArgs = @("--platform", "linux/$Arch")
}

$OUT = "dist"
New-Item -ItemType Directory -Force -Path $OUT | Out-Null

$archLabel = if ($Arch) { $Arch } else { "本机架构" }
Write-Host "=== Artoo 离线包构建 ($archLabel) ===" -ForegroundColor Green

Write-Host "[1/4] 构建应用镜像..." -ForegroundColor Cyan
$graphBuildArgs = @()
if ($WithGraph) {
    $graphBuildArgs = @("--build-arg", "WITH_GRAPH=true")
    Write-Host "  （含知识图谱依赖：Neo4j 驱动）" -ForegroundColor DarkGray
}
docker build @platformArgs @graphBuildArgs -t artoo-backend:latest backend/
if ($LASTEXITCODE -ne 0) { throw "后端镜像构建失败" }
docker build @platformArgs -t artoo-frontend:latest frontend/
if ($LASTEXITCODE -ne 0) { throw "前端镜像构建失败" }

Write-Host "[2/4] 导出应用镜像..." -ForegroundColor Cyan
docker save @savePlatformArgs artoo-backend:latest artoo-frontend:latest -o "$OUT\app-images.tar"
if ($LASTEXITCODE -ne 0) { throw "导出应用镜像失败" }

if (-not $AppOnly) {
    Write-Host "[3/4] 拉取并导出中间件镜像..." -ForegroundColor Cyan
    $infraImages = @(
        "postgres:16-alpine",
        "redis:7-alpine",
        "milvusdb/milvus:v2.5.4",
        "quay.io/coreos/etcd:v3.5.25",
        "minio/minio:RELEASE.2024-05-28T17-19-04Z"
    )
    if ($WithGraph) {
        $infraImages += "neo4j:5-community"
        Write-Host "  （含 Neo4j 镜像）" -ForegroundColor DarkGray
    }
    foreach ($img in $infraImages) {
        # 经典 overlay2 存储下，一个 tag 只能存在一个架构。若本地残留的是其它架构副本，
        # 直接 pull 不会换架构，导出的包与目标架构不符（且 save --platform 会静默拿旧架构），
        # 因此先删掉架构不符的本地副本再拉取。
        if ($Arch) {
            $localArch = (docker image inspect $img --format '{{.Architecture}}' 2>$null)
            if ($localArch -and $localArch -ne $Arch) {
                Write-Host "  本地 $img 为 $localArch，与目标 $Arch 不符，删除后重新拉取..." -ForegroundColor DarkGray
                docker rmi $img 2>$null | Out-Null
            }
        }
        docker pull @platformArgs $img
        if ($LASTEXITCODE -ne 0) { throw "拉取镜像失败: $img" }
    }
    docker save @savePlatformArgs $infraImages -o "$OUT\infra-images.tar"
    if ($LASTEXITCODE -ne 0) { throw "导出中间件镜像失败" }
} else {
    Write-Host "[3/4] 跳过中间件镜像（-AppOnly）" -ForegroundColor Cyan
    # 清理上次完整构建残留的中间件包，避免压缩时误带
    if (Test-Path "$OUT\infra-images.tar") {
        Remove-Item "$OUT\infra-images.tar" -Force
        Write-Host "  已清理旧的 infra-images.tar" -ForegroundColor DarkGray
    }
}

Write-Host "[4/4] 复制部署文件..." -ForegroundColor Cyan
Copy-Item docker-compose.yml "$OUT\"
Copy-Item .env.example "$OUT\"
New-Item -ItemType Directory -Force -Path "$OUT\deploy" | Out-Null
Copy-Item deploy\milvus-user.yaml "$OUT\deploy\"
Copy-Item deploy\install.sh "$OUT\"
# Milvus 拓扑切换用的数据清除脚本：必须随包交付，否则运维在服务器上无脚本可执行
# （与 build.sh 保持一致；脚本自身会向上一级找 docker-compose.yml，两种布局都兼容）。
Copy-Item deploy\reset-knowledge-data.sh "$OUT\deploy\"
# 运维部署手册：随包交付，运维在服务器上可直接查阅。
Copy-Item deploy\DEPLOY.md "$OUT\"
# 前端运行时配置：compose 把 ./frontend/public/config.js 只读挂载进容器覆盖镜像内默认。
# 离线包必须带上此文件，否则宿主路径不存在时 Docker 会按目录创建，导致挂载失败。
New-Item -ItemType Directory -Force -Path "$OUT\frontend\public" | Out-Null
Copy-Item frontend\public\config.js "$OUT\frontend\public\config.js"

if ($Tar) {
    Write-Host "[5/5] 生成压缩包 artoo-deploy.tar.gz..." -ForegroundColor Cyan
    if (Test-Path "artoo-deploy.tar.gz") { Remove-Item "artoo-deploy.tar.gz" -Force }
    tar -czf artoo-deploy.tar.gz -C $OUT .
    if ($LASTEXITCODE -ne 0) { throw "压缩失败" }
}

Write-Host ""
Write-Host "=== 完成 ===" -ForegroundColor Green
if ($Tar) {
    $tarSize = [math]::Round((Get-Item "artoo-deploy.tar.gz").Length / 1GB, 2)
    Write-Host "输出: artoo-deploy.tar.gz ($tarSize GB)"
} else {
    $size = [math]::Round((Get-ChildItem -Recurse $OUT | Measure-Object -Property Length -Sum).Sum / 1GB, 2)
    Write-Host "输出: $OUT\ ($size GB)"
    Write-Host ""
    Write-Host "提示: 已产出 dist\，可先修改 dist\ 内的配置（.env.example / docker-compose.yml）" -ForegroundColor DarkGray
    Write-Host "      改完后手动压缩，或重新执行加 -Tar 自动压缩：" -ForegroundColor DarkGray
    Write-Host "      tar -czf artoo-deploy.tar.gz -C dist ." -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host " 部署步骤" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""
Write-Host "1. 上传 artoo-deploy.tar.gz 到服务器" -ForegroundColor Cyan
Write-Host ""
Write-Host "2. 服务器端解压:" -ForegroundColor Cyan
Write-Host "   mkdir -p artoo && tar -xzf artoo-deploy.tar.gz -C artoo && cd artoo"
Write-Host ""
Write-Host "3. 首次部署（脚本会自动授权，自动探测 Compose V1/V2）:" -ForegroundColor Cyan
Write-Host "   bash install.sh"
Write-Host ""
Write-Host "4. 后续更新（替换 app-images.tar 后）:" -ForegroundColor Cyan
Write-Host "   bash install.sh update"
Write-Host ""
