"""고객 포털 화면 — 로그인 · 처리 내역 · 처리 내역 상세.

마크업과 CSS 는 기획 시안(wl-portal-proto/viewer)을 그대로 쓴다. NiceGUI 위젯 대신
시안 HTML 을 그대로 뱉고 `static/style.css`(시안 사본)를 입힌다 — 시안과 화면이
달라지면 확인용으로서의 값이 없기 때문이다.

세션은 `request.session`(Starlette) 을 쓴다. `app.storage.user` 는 UI 컨텍스트에서만
되므로 폼 POST 라우트에서 함께 쓸 수 없다.

고객에게 나가는 값은 전부 app.whitelist.portal 의 customer_* 를 거친다 — 반출 범위
(S/N·PO·금액 제외)를 한 곳에서만 지키기 위해서다.
"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from fastapi import Form, Request
from fastapi.responses import RedirectResponse
from nicegui import app as ng_app
from nicegui import ui

from app.whitelist import portal as PT

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    ng_app.add_static_files("/wl", str(STATIC_DIR))

# 고객에게 보이는 상태 → 시안 배지 클래스
_BADGE = {"접수": "received", "진행 중": "registering",
          "완료": "completed", "반려": "rejected"}


def e(v) -> str:
    """HTML 이스케이프 — 화면에 넣는 값은 전부 이 함수를 거친다."""
    return html.escape(str(v if v is not None else ""))


def _head() -> None:
    ui.add_head_html('<link rel="stylesheet" href="/wl/style.css">')
    # NiceGUI 기본 래퍼가 시안 레이아웃(전체폭 헤더 + .wrap)을 방해하지 않게 한다.
    ui.add_head_html(
        "<style>.nicegui-content{padding:0!important;gap:0!important;"
        "display:block!important}body{background:#f4f6f5}</style>"
    )


def _me(request: Request) -> dict | None:
    s = request.session
    if not s.get("wl_authed"):
        return None
    return {"account_id": s.get("wl_account_id"), "username": s.get("wl_username"),
            "company_id": s.get("wl_company_id"), "company": s.get("wl_company"),
            "must_change": bool(s.get("wl_must_change"))}


def _header(me: dict | None, active: str = "") -> str:
    nav = ""
    who = ""
    if me:
        def b(key: str, label: str, href: str) -> str:
            on = " class='on'" if key == active else ""
            return f"<button{on} onclick=\"location.href='{href}'\">{label}</button>"

        nav = ("<nav>" + b("hist", "처리 내역", "/history")
               + b("pw", "비밀번호 변경", "/password")
               + b("out", "로그아웃", "/logout") + "</nav>")
        who = f"<div class='who'>{e(me['company'])} · {e(me['username'])}</div>"
    return ("<header><div class='brand'>Point Mobile <small>WhiteList</small></div>"
            + nav + who + "</header>")


def _tag() -> str:
    return "<div class='proto-tag'>데모 · 외부 서버(8082) 가정 화면입니다</div>"


def _shell(body: str, me: dict | None = None, active: str = "") -> None:
    ui.html(_header(me, active) + f"<div class='wrap'>{body}</div>" + _tag(),
            sanitize=False)


# ── 로그인 ────────────────────────────────────────────────────────────────
@ui.page("/")
def index(request: Request):
    me = _me(request)
    if not me:
        ui.navigate.to("/login")
    else:
        ui.navigate.to("/password" if me["must_change"] else "/history")


@ui.page("/login")
def login_page(request: Request, err: str = ""):
    _head()
    warn = (f"<p class='muted' style='color:#d64545;font-size:12.5px;"
            f"text-align:center;margin:10px 0 0'>{e(err)}</p>" if err else "")
    _shell(f"""
<form method="post" action="/login">
  <div class="card" style="max-width:380px;margin:40px auto">
    <h3 style="text-align:center;margin-bottom:18px">WhiteList 로그인</h3>
    <div class="field"><label>아이디</label>
      <input class="mono" name="username" autofocus autocomplete="username"></div>
    <div class="field"><label>비밀번호</label>
      <input type="password" name="password" autocomplete="current-password"></div>
    <button class="btn" style="width:100%">로그인</button>{warn}
    <p class="muted" style="font-size:12px;text-align:center;margin:14px 0 0">
      <a class="linkish" href="/forgot">비밀번호를 잊으셨나요?</a>
    </p>
    <p class="muted" style="font-size:12px;text-align:center;margin:8px 0 0">
      계정은 Point Mobile 담당자가 발급합니다.
    </p>
  </div>
</form>""")


@ng_app.post("/login")
def login_post(request: Request, username: str = Form(""), password: str = Form("")):
    got = PT.authenticate(username, password)
    if not got:
        return RedirectResponse("/login?err=아이디 또는 비밀번호가 맞지 않습니다.",
                                status_code=303)
    request.session.update({
        "wl_authed": True, "wl_account_id": got["account_id"],
        "wl_username": got["username"], "wl_company_id": got["company_id"],
        "wl_company": got["company"], "wl_must_change": got["must_change"],
    })
    return RedirectResponse("/password" if got["must_change"] else "/history",
                            status_code=303)


@ng_app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ── 처리 내역 ─────────────────────────────────────────────────────────────
@ui.page("/history")
def history_page(request: Request, q: str = "", state: str = ""):
    me = _me(request)
    if not me:
        ui.navigate.to("/login")
        return
    if me["must_change"]:
        ui.navigate.to("/password")
        return
    _head()

    rows = PT.customer_requests(me["company_id"])
    now = datetime.now()
    n_prog = sum(1 for r in rows if r["state"] not in ("완료", "반려"))
    u_prog = sum(r["devices"] for r in rows if r["state"] not in ("완료", "반려"))
    month = [r for r in rows if r["state"] == "완료" and r["created_at"]
             and r["created_at"].year == now.year and r["created_at"].month == now.month]
    total_units = sum(r["devices"] for r in rows if r["state"] == "완료")
    since = min((r["created_at"] for r in rows if r["created_at"]), default=None)

    shown = rows
    if q:
        k = q.lower()
        shown = [r for r in shown if k in r["id"].lower() or k in r["models"].lower()]
    if state:
        shown = [r for r in shown if r["state"] == state]

    opts = "".join(
        f"<option{' selected' if state == v else ''}>{e(v)}</option>"
        for v in ("진행 중", "완료", "반려"))
    body_rows = "".join(f"""
        <tr>
          <td class="mono">{e(r['id'])}</td><td>{str(r['created_at'])[:10]}</td>
          <td>{e(r['models'])}</td>
          <td class="num">{r['devices']:,}</td>
          <td><span class="b {_BADGE.get(r['state'], 'ghost')}">{e(r['state'])}</span></td>
          <td class="right"><a class="linkish" href="/history/{e(r['id'])}">상세</a></td>
        </tr>""" for r in shown)
    if not shown:
        body_rows = ("<tr><td colspan='6' class='muted' style='padding:18px 10px'>"
                     "표시할 요청이 없습니다. 담당자가 요청을 접수하면 이곳에 나타납니다."
                     "</td></tr>")

    _shell(f"""
<h2>처리 내역</h2>
<p class="sub">{e(me['company'])} 이(가) 신청한 건입니다.</p>

<div class="kpis">
  <div class="kpi"><div class="l">진행 중</div><div class="v">{n_prog}</div>
    <div class="n">단말 {u_prog:,}대</div></div>
  <div class="kpi"><div class="l">이번 달 완료</div><div class="v">{len(month)}</div>
    <div class="n">단말 {sum(r['devices'] for r in month):,}대</div></div>
  <div class="kpi"><div class="l">누적 등록 단말</div><div class="v">{total_units:,}</div>
    <div class="n">{since.strftime('%Y-%m') if since else '—'} 부터</div></div>
</div>

<div class="card">
  <form method="get" action="/history">
    <div class="row" style="margin-bottom:12px">
      <div><input name="q" value="{e(q)}" placeholder="접수번호 · 모델 검색"></div>
      <div style="max-width:180px"><select name="state" onchange="this.form.submit()">
        <option value="">전체 상태</option>{opts}</select></div>
    </div>
  </form>
  <table>
    <thead><tr>
      <th>접수번호</th><th>신청일</th><th>모델</th>
      <th class="num">단말</th><th>진행</th><th></th>
    </tr></thead>
    <tbody>{body_rows}</tbody>
  </table>
</div>""", me, "hist")


# ── 처리 내역 상세 ────────────────────────────────────────────────────────
@ui.page("/history/{request_id}")
def detail_page(request: Request, request_id: str):
    me = _me(request)
    if not me:
        ui.navigate.to("/login")
        return
    if me["must_change"]:
        ui.navigate.to("/password")
        return
    _head()

    # 소속이 다른 건은 조회 자체가 되지 않는다(customer_detail 이 None 을 준다).
    d = PT.customer_detail(me["company_id"], request_id)
    if d is None:
        _shell("<div class='card'>해당 요청을 찾을 수 없습니다.</div>"
               "<div class='right'><a class='btn flat' href='/history'>목록으로</a></div>",
               me, "hist")
        return

    steps = ""
    last_done = max((i for i, s in enumerate(d["steps"]) if s["at"]), default=-1)
    for i, s in enumerate(d["steps"]):
        cls = "done" if s["at"] else ""
        if i == last_done and d["state"] not in ("완료", "반려"):
            cls = "now"
        when = str(s["at"])[:16] if s["at"] else "—"
        desc = f"<div class='d'>{e(s['desc'])}</div>" if s["desc"] else ""
        steps += (f"<li class='{cls}'><div class='t'>{e(when)}</div>"
                  f"<div class='h'>{e(s['title'])}</div>{desc}</li>")

    _shell(f"""
<h2><span class="mono">{e(d['id'])}</span>
  <span class="b {_BADGE.get(d['state'], 'ghost')}">{e(d['state'])}</span></h2>
<p class="sub">{str(d['created_at'])[:10]} 신청 · {e(d['models'])} ·
  Android {e(d['os'])} · 단말 {d['devices']:,}대</p>

<div class="row">
  <div style="flex:1.15">
    <div class="card">
      <h3>진행 상황</h3>
      <ul class="tl">{steps}</ul>
    </div>

    <div class="card">
      <h3>신청 내용</h3>
      <table><tbody>
        <tr><th style="width:26%">모델</th><td>{e(d['models'])}</td></tr>
        <tr><th>OS</th><td>Android {e(d['os'])}</td></tr>
        <tr><th>단말</th><td>{d['devices']:,}대</td></tr>
        <tr><th>상태</th><td>{e(d['state_desc'])}</td></tr>
      </tbody></table>
    </div>
  </div>

  <div style="flex:1">
    <div class="card">
      <h3>단말 처리 요약</h3>
      <div class="kpis" style="margin-bottom:10px">
        <div class="kpi"><div class="l">신청</div><div class="v">{d['devices']:,}</div></div>
        <div class="kpi"><div class="l">처리</div>
          <div class="v">{(d['devices'] if d['state'] == '완료' else 0):,}</div></div>
      </div>
      <div class="bar"><i style="width:{100 if d['state'] == '완료' else 0}%"></i></div>
      <p class="muted" style="font-size:12px;margin:10px 0 0">
        단말 일련번호와 구매 정보는 이 화면에 표시되지 않습니다.
        필요하시면 담당자에게 문의해 주세요.
      </p>
    </div>
  </div>
</div>

<div class="right"><a class="btn flat" href="/history">목록으로</a></div>""",
           me, "hist")


# ── 비밀번호 변경 ─────────────────────────────────────────────────────────
@ui.page("/password")
def password_page(request: Request, err: str = ""):
    me = _me(request)
    if not me:
        ui.navigate.to("/login")
        return
    _head()
    forced = me["must_change"]
    note = ("<div class='note'><b>초기 비밀번호를 쓰고 계십니다.</b><br>"
            "처음 발급받은 비밀번호는 담당자도 아는 값입니다. "
            "본인만 아는 값으로 바꿔야 처리 내역을 보실 수 있습니다.</div>"
            if forced else "")
    warn = (f"<p style='color:#d64545;font-size:12.5px;margin:10px 0 0'>{e(err)}</p>"
            if err else "")
    cancel = ("" if forced else
              "<p style='text-align:center;margin:12px 0 0'>"
              "<a class='linkish' href='/history'>취소</a></p>")
    _shell(f"""
<form method="post" action="/password">
  <div class="card" style="max-width:420px;margin:30px auto">
    <h3>비밀번호 변경</h3>{note}
    <div class="field"><label>현재 비밀번호</label>
      <input type="password" name="current" autofocus></div>
    <div class="field"><label>새 비밀번호</label>
      <input type="password" name="new"></div>
    <div class="field"><label>새 비밀번호 확인</label>
      <input type="password" name="again"></div>
    <p class="muted" style="font-size:12px;margin:0 0 12px">
      {PT.MIN_PASSWORD_LEN}자 이상, 초기 비밀번호와 다른 값</p>
    <button class="btn" style="width:100%">변경</button>{warn}{cancel}
  </div>
</form>""", me, "pw")


@ng_app.post("/password")
def password_post(request: Request, current: str = Form(""), new: str = Form(""),
                  again: str = Form("")):
    me = _me(request)
    if not me:
        return RedirectResponse("/login", status_code=303)
    ok, msg = PT.change_password(me["account_id"], current, new, again)
    if not ok:
        return RedirectResponse(f"/password?err={msg}", status_code=303)
    request.session["wl_must_change"] = False
    return RedirectResponse("/history", status_code=303)


# ── 비밀번호 재설정 ───────────────────────────────────────────────────────
@ui.page("/forgot")
def forgot_page(request: Request, sent: str = ""):
    _head()
    done = ("<div class='note info'>등록된 이메일로 재설정 안내를 보냈습니다. "
            "메일이 오지 않으면 담당자에게 문의해 주세요.</div>" if sent else "")
    _shell(f"""
<form method="post" action="/forgot">
  <div class="card" style="max-width:400px;margin:40px auto">
    <h3>비밀번호 재설정</h3>
    <p class="muted" style="font-size:12.5px;margin:0 0 12px">
      아이디 또는 등록된 이메일을 넣어 주세요. 재설정 링크를 메일로 보내 드립니다.</p>
    {done}
    <div class="field"><label>아이디 또는 이메일</label>
      <input class="mono" name="who" autofocus></div>
    <button class="btn" style="width:100%">재설정 링크 받기</button>
    <p style="text-align:center;margin:12px 0 0">
      <a class="linkish" href="/login">로그인으로</a></p>
  </div>
</form>""")


@ng_app.post("/forgot")
def forgot_post(request: Request, who: str = Form("")):
    res = PT.request_reset(who)
    if res.get("found"):
        ok, err = PT.send_reset_mail(res["email"], res["username"], res["url"])
        if not ok:
            # 메일이 못 나가도 화면 안내는 같다(계정 존재를 드러내지 않는다).
            print(f"[portal] 재설정 메일 실패({err}) — {res['username']} "
                  f"링크: {res['url']}", flush=True)
    return RedirectResponse("/forgot?sent=1", status_code=303)


@ui.page("/reset")
def reset_page(token: str = "", err: str = ""):
    _head()
    got = PT.verify_reset(token)
    if got is None:
        _shell("""
<div class="card" style="max-width:400px;margin:40px auto">
  <h3>비밀번호 재설정</h3>
  <p style="color:#d64545">링크가 만료되었거나 이미 사용되었습니다.</p>
  <a class="btn" href="/forgot" style="width:100%;text-align:center">다시 요청하기</a>
</div>""")
        return
    warn = (f"<p style='color:#d64545;font-size:12.5px;margin:10px 0 0'>{e(err)}</p>"
            if err else "")
    _shell(f"""
<form method="post" action="/reset">
  <input type="hidden" name="token" value="{e(token)}">
  <div class="card" style="max-width:420px;margin:40px auto">
    <h3>비밀번호 재설정</h3>
    <p class="muted" style="font-size:12.5px;margin:0 0 12px">
      {e(got['username'])} 님의 새 비밀번호를 정해 주세요.</p>
    <div class="field"><label>새 비밀번호</label>
      <input type="password" name="new" autofocus></div>
    <div class="field"><label>새 비밀번호 확인</label>
      <input type="password" name="again"></div>
    <p class="muted" style="font-size:12px;margin:0 0 12px">
      {PT.MIN_PASSWORD_LEN}자 이상, 초기 비밀번호와 다른 값</p>
    <button class="btn" style="width:100%">설정</button>{warn}
  </div>
</form>""")


@ng_app.post("/reset")
def reset_post(token: str = Form(""), new: str = Form(""), again: str = Form("")):
    ok, msg = PT.apply_reset(token, new, again)
    if not ok:
        return RedirectResponse(f"/reset?token={token}&err={msg}", status_code=303)
    return RedirectResponse("/login", status_code=303)
