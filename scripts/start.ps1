# tw-next 를 콘솔 창 없이 띄운다 — API(8180, 이 PC 안에서만) + 화면(3080, 사내망).
# 개발용 로그인(TWN_DEV_LOGIN_AS)은 여기서 켜지 않는다.
$root = Split-Path -Parent $PSScriptRoot
$py = "C:\Users\POINTMOBILE\dev\tw-automation\.venv\Scripts\python.exe"   # 기존 앱의 venv 를 빌려 쓴다(읽기만)
$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logs | Out-Null

foreach ($port in 8180, 3080) {
    $p = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
    if ($p) { Stop-Process -Id $p -Force }
}
Remove-Item Env:TWN_DEV_LOGIN_AS -ErrorAction SilentlyContinue
$env:PYTHONIOENCODING = "utf-8"

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
