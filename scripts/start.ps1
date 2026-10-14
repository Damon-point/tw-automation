# tw-next 를 콘솔 창 없이 띄운다 — API(8180, 이 PC 안에서만) + 화면(3080, 사내망).
# 개발용 로그인(TWN_DEV_LOGIN_AS)은 여기서 켜지 않는다.
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "backend\.venv\Scripts\python.exe"   # 3080 전용 venv
$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logs | Out-Null

foreach ($port in 8180, 3080) {
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

Start-Process -FilePath $py -ArgumentList "-X utf8 -m uvicorn main:app --host 127.0.0.1 --port 8180" `
    -WorkingDirectory (Join-Path $root "api") -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs "api.out.log") -RedirectStandardError (Join-Path $logs "api.err.log")

$web = Join-Path $root "web"
if (-not (Test-Path (Join-Path $web ".next\BUILD_ID"))) {
    Push-Location $web; npx next build; Pop-Location
}
Start-Process -FilePath "cmd.exe" -ArgumentList "/c npx next start -p 3080 -H 0.0.0.0" `
    -WorkingDirectory $web -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs "web.out.log") -RedirectStandardError (Join-Path $logs "web.err.log")
"started: http://192.168.121.35:3080"
