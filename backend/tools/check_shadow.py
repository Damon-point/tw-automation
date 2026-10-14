"""검증 모드 규칙 확인 — 외부 쓰기 함수가 하나도 밖으로 나가지 않는가.

네트워크·SMTP·Dropbox·Playwright 를 모두 '부르면 터지게' 바꿔 놓고 각 함수를 실제 쓰기
인자(allow_write=True 등)로 부른다. 검증 모드면 터지지 않고 기록만 남아야 한다.
마지막에 운영 모드로 바꿔 같은 함수가 실제로 나가려 하는지(=터지는지)도 본다 — 게이트가
늘 닫혀 있기만 한 가짜가 아님을 확인하는 음성 검사다.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["TWN_LIVE_WRITES"] = "0"


class Boom(Exception):
    pass


def boom(*a, **k):
    raise Boom("외부로 나가려 함")


import requests  # noqa: E402
import smtplib  # noqa: E402
import urllib.request  # noqa: E402

requests.Session.request = boom
requests.post = requests.put = requests.patch = requests.delete = boom
smtplib.SMTP = boom
urllib.request.urlopen = boom

from app import app_settings, shadow  # noqa: E402

shadow.LOG_PATH = Path(tempfile.mkdtemp()) / "shadow.jsonl"
app_settings._cache["live_writes"] = "0"

from app import user_mailer  # noqa: E402
from app.integrations import dropbox_client as D  # noqa: E402
from app.integrations import mailchimp as MC  # noqa: E402
from app.integrations import service_portal as SP  # noqa: E402
from app.integrations import confluence_reader as CR  # noqa: E402
from app.integrations import fota_system as FS  # noqa: E402
from app.pipeline import kb_new_page as KB  # noqa: E402
from app.whitelist import fota_db as FDB  # noqa: E402
from app.issuetrack import confluence as SBC  # noqa: E402

D._make_dbx = boom
SP._session = boom
FDB._connect = boom
FDB._guard = lambda sns, dry_run: (FDB.DbReport(dry_run=dry_run), [])

tmp = Path(tempfile.mkdtemp()) / "a.txt"
tmp.write_text("x")
cfg = MC.MailchimpConfig(api_key="k-us1", list_id="L", server_prefix="us1", tag="", from_name="", reply_to="", subject_default="")

calls = {
    "mail": lambda: user_mailer.send_as_user(1, "damon.park@pointmobile.com", "s", "b"),
    "dbx.upload_files": lambda: D.upload_files("t", [str(tmp)], "/x"),
    "dbx.upload_bytes": lambda: D.upload_bytes("t", "a", b"x", "/x"),
    "dbx.upload_and_copy": lambda: D.upload_and_copy("t", str(tmp), ["/x/a", "/y/a"]),
    "dbx.delete": lambda: D.delete_path("t", "/x"),
    "dbx.link": lambda: D.shared_link_for("t", "/x/a"),
    "sp.create": lambda: SP.create_doc(1, {}, "t", "c", allow_write=True),
    "sp.save": lambda: SP.save_real_cont(1, "5", "c", allow_write=True),
    "cf.save": lambda: CR.save_release_note(1, "1", "<p/>", 1, "t", allow_write=True),
    "kb.create": lambda: KB.create_page(1, "t", "<p/>", allow_write=True),
    "mc.create": lambda: MC.create_campaign(cfg, "s", "t", list_id="L"),
    "mc.content": lambda: MC.set_content(cfg, "c", "<p/>"),
    "mc.subject": lambda: MC.set_subject(cfg, "c", "s"),
    "mc.image": lambda: MC.upload_image(cfg, "a.png", b"x"),
    "mc.send": lambda: MC.send_campaign(cfg, "c", allow_send=True),
    "fota_db.date": lambda: FDB.change_registration_date(["S1"], __import__("datetime").date.today(), dry_run=False),
}

bad = []
for name, fn in calls.items():
    try:
        r = fn()
    except Boom:
        bad.append(f"{name}: 검증 모드인데 밖으로 나가려 함")
        continue
    except Exception as e:  # noqa: BLE001
        bad.append(f"{name}: {type(e).__name__}: {e}")
        continue
    if name == "fota_db.date" and not r.dry_run:
        bad.append("fota_db.date: dry_run 이 풀림")
    if name == "mc.send" and not (r[0] and "검증 모드" in r[1]):
        bad.append(f"mc.send: {r}")

# FOTA 웹 등록 — 쓰기 대신 dry-run 이 되어야 한다(세션 열기는 읽기라 막지 않으니 로그인 함수를 바꾼다)
src = Path(FS.__file__).read_text(encoding="utf-8")
if src.count('_shadow.blocked("fota_web"') != 3:
    bad.append("fota_system: register 3곳에 게이트가 없음")
sb = Path(SBC.__file__).read_text(encoding="utf-8")
if '_shadow.blocked("sb_confluence"' not in sb:
    bad.append("issuetrack/confluence: _req 게이트 없음")
wl = Path(sys.modules["app.whitelist.fota_db"].__file__).with_name("fota.py").read_text(encoding="utf-8")
if '_shadow.blocked("fota_whitelist"' not in wl:
    bad.append("whitelist/fota: 업로드 게이트 없음")
fs = Path(D.__file__).with_name("fileserver.py").read_text(encoding="utf-8")
if '_shadow.blocked("fileserver"' not in fs:
    bad.append("fileserver: 업로드 게이트 없음")

logged = shadow.recent(100)
systems = {r["system"] for r in logged}
for s in ("mail", "dropbox", "service_portal", "confluence", "mailchimp", "fota_db"):
    if s not in systems:
        bad.append(f"기록 없음: {s}")

# 음성 검사 — 운영 모드에선 실제로 나가려 해야 한다
app_settings._cache["live_writes"] = "1"
for name in ("dbx.delete", "sp.save", "mc.content"):
    try:
        r = calls[name]()
        if r in ((True, ""),):
            bad.append(f"운영 모드인데 {name} 가 밖으로 나가지 않음")
    except Boom:
        pass
app_settings._cache["live_writes"] = "0"

print(f"기록 {len(logged)}건, 시스템 {sorted(systems)}")
if bad:
    print("FAIL")
    print("\n".join(bad))
    sys.exit(1)
print("OK")
