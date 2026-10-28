# tw-automation-v2 를 콘솔 창 없이 띄운다 — API(8180, 이 PC 안에서만) + 화면(8080, 사내망).
# 2026-10-28 부터 운영 서버 — 예전 8080(tw-automation)을 내리고 이 화면이 8080 을 쓴다.
# tw-automation\watchdog.ps1 이 8080 · 8180 이 닫혀 있으면 이 스크립트를 부른다(재부팅 뒤 자동 기동).
# 개발용 로그인(TWN_DEV_LOGIN_AS)은 여기서 켜지 않는다.
param([int]$WebPort = 8080)
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "backend\.venv\Scripts\python.exe"   # 3080 전용 venv
$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logs | Out-Null

foreach ($port in 8180, $WebPort) {
    $p = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
    if ($p) { Stop-Process -Id $p -Force }
}
Remove-Item Env:TWN_DEV_LOGIN_AS -ErrorAction SilentlyContinue
$env:PYTHONIOENCODING = "utf-8"

# 로컬 PostgreSQL(127.0.0.1:5432, 데이터 C:\Users\POINTMOBILE\pgdata\twnext) — 꺼져 있으면 먼저 켠다.
# 서비스로 설치하지 않았다(관리자 권한 없음). 이 스크립트가 켜고, 3080 을 다시 띄워도 끄지 않는다.
$pgBin = "C:\Users\POINTMOBILE\pgsql\bin"
$pgData = "C:\Users\POINTMOBILE\pgdata\twnext"
if (-not (Get-NetTCPConnection -LocalPort 5432 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath (Join-Path $pgBin "pg_ctl.exe") -ArgumentList "-D `"$pgData`" -l `"$pgData\startup.log`" start" -WindowStyle Hidden
    for ($i = 0; $i -lt 30 -and -not (Get-NetTCPConnection -LocalPort 5432 -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 500 }
    if (-not (Get-NetTCPConnection -LocalPort 5432 -State Listen -ErrorAction SilentlyContinue)) { throw "PostgreSQL 이 켜지지 않았습니다 — $pgData\log 를 보세요" }
}
# 포트가 열려도 PC 가 갑자기 꺼진 뒤라면 복구하는 동안 접속을 거부한다("database system is starting up").
# 접속을 받을 때까지(최대 2분) 기다린 뒤 API 를 띄운다 — 2026-10-15 재부팅 뒤 API 가 이것 때문에 죽었다.
for ($i = 0; $i -lt 120; $i++) {
    & (Join-Path $pgBin "pg_isready.exe") -h 127.0.0.1 -p 5432 -q
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 1
}
if ($LASTEXITCODE -ne 0) { throw "PostgreSQL 이 접속을 받지 않습니다 — $pgData\log 를 보세요" }

Start-Process -FilePath $py -ArgumentList "-X utf8 -m uvicorn main:app --host 127.0.0.1 --port 8180" `
    -WorkingDirectory (Join-Path $root "api") -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs "api.out.log") -RedirectStandardError (Join-Path $logs "api.err.log")

$web = Join-Path $root "web"
if (-not (Test-Path (Join-Path $web ".next\BUILD_ID"))) {
    Push-Location $web; npx next build; Pop-Location
}
Start-Process -FilePath "cmd.exe" -ArgumentList "/c npx next start -p $WebPort -H 0.0.0.0" `
    -WorkingDirectory $web -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs "web.out.log") -RedirectStandardError (Join-Path $logs "web.err.log")
"started: http://192.168.121.35:$WebPort"
