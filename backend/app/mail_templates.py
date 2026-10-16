# -*- coding: utf-8 -*-
"""앱이 보내는 메일의 **문구 모음**. 화면([설정] → 메일 문구)에서 고칠 수 있다.

여기 있는 것은 기본 문구다. 사람이 고치면 mail_templates 표에 그것만 저장되고, 고친
적이 없으면 기본 문구가 그대로 쓰인다. '기본값으로 되돌리기'는 저장된 행을 지우는 일이다.

문구 안의 {{이름}} 은 보낼 때 실제 값으로 바뀐다. 각 문구가 어떤 값을 받는지는 vars 에
적어 두었고, 편집 화면이 그 설명을 그대로 보여 준다. {{앱주소}}·{{문의주소}}·{{오늘}}
은 어느 문구에서나 쓸 수 있다(AUTO 참조).

**서명은 본문에 넣지 않는다.** 보낼 때 user_mailer 가 보내는 사람 이름을 뒤에 붙인다
(시스템 명의 메일은 서명이 없으므로, 그런 문구는 맺음말을 본문에 둔다).

문구를 고쳐도 **보내는 자리는 바뀌지 않는다** — 누구에게 언제 나가는지는 코드가 정한다.
아는 이름이 아닌 {{...}} 는 손대지 않고 그대로 둔다(오타 때문에 발송이 실패하지 않게).
저장할 때 편집 화면이 그런 이름을 짚어 준다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from app.database import get_session
from app.models import MailTemplate

_VAR = re.compile(r"\{\{\s*([^{}\s]+)\s*\}\}")

#: 어느 문구에서나 쓸 수 있는 값. 보낼 때 자동으로 채워진다.
AUTO: dict[str, str] = {
    "앱주소": "TW Automation 주소 — 'http://192.168.121.35:8080'",
    "문의주소": "문의를 받을 시스템 관리자 메일 주소",
    "오늘": "보내는 날짜 — '2026-08-25'",
}


def auto_values() -> dict:
    from app.user_mailer import app_url, system_admin_emails

    admins = system_admin_emails()
    return {"앱주소": app_url(),
            "문의주소": admins[0] if admins else "",
            "오늘": datetime.now().strftime("%Y-%m-%d")}


@dataclass
class Tpl:
    key: str
    title: str                     # 화면에 보일 이름
    group: str                     # 묶음(WhiteList · 계정 · 알림 · 검수 · License)
    when: str                      # 언제 나가는가
    to: str                        # 누구에게 가는가
    subject: str
    body: str
    vars: dict[str, str] = field(default_factory=dict)   # 이름 -> 설명(예시)


TEMPLATES: dict[str, Tpl] = {}


def _add(t: Tpl) -> None:
    TEMPLATES[t.key] = t


_AUTO_NOTE = "이 메일은 TW Automation 이 자동 발송했습니다."

# ── WhiteList ────────────────────────────────────────────────────────────────
# 실제로 보낸 회신(2026-08~10, 메일함)을 따랐다 — 요청 스레드에 'RE:' 로 답하고, 인사 다음 줄에 호칭,
# S/N 은 'SN: a, b' 한 줄, 끝은 '업무에 참조 부탁드립니다.' (2026-10-16)
_add(Tpl(
    key="wl_reply_done_ko", title="WhiteList 등록 완료 회신 (한국어)", group="WhiteList",
    when="[WhiteList] 처리 화면에서 회신을 만들 때", to="요청하신 분",
    subject="RE: {{원본제목}}",
    body=(
        "안녕하세요.\n"
        "\n"
        "{{호칭줄}}"
        "요청하신 아래 SN에 대하여 FOTA 서버 등록 완료하였습니다.\n"
        "{{단말목록}}\n"
        "\n"
        "업무에 참조 부탁드립니다.\n"
        "감사합니다."
    ),
    vars={"원본제목": "요청 메일 제목(RE: 는 겹쳐 붙지 않는다)",
          "호칭줄": "'염서연 대리님' 한 줄 + 빈 줄 — 그분께 직접 보낸 회신에서 배운 호칭, 없으면 '이름님'",
          "호칭": "이름 부분 — ', 홍길동님' (예전 문구용)",
          "대수": "단말 수 — '3'",
          "단말목록": "'SN: 2032210078, 2329110473' (많으면 10개씩 줄바꿈)"},
))

_add(Tpl(
    key="wl_reply_done_en", title="WhiteList 등록 완료 회신 (영문)", group="WhiteList",
    when="요청 메일이 영문일 때", to="요청하신 분",
    subject="RE: {{원본제목}}",
    body=(
        "Hello{{호칭}},\n"
        "\n"
        "The following device(s) have been registered on the FOTA server.\n"
        "{{단말목록}}\n"
        "\n"
        "Please refer to this for your work.\n"
        "Thank you."
    ),
    vars={"원본제목": "요청 메일 제목", "호칭줄": "'Gil-dong Hong,' 한 줄 + 빈 줄",
          "호칭": "이름 부분 — ' Gil-dong Hong' (이름이 없으면 비어 있음)",
          "대수": "단말 수", "단말목록": "'SN: ...'"},
))

_add(Tpl(
    key="wl_reply_date_ko", title="WhiteList 등록일 변경 회신 (한국어)", group="WhiteList",
    when="등록이 아니라 등록일만 바꿨을 때", to="요청하신 분",
    subject="RE: {{원본제목}}",
    body=(
        "안녕하세요.\n"
        "\n"
        "{{호칭줄}}"
        "요청하신 아래 SN {{대수}}건에 대하여 등록일{{변경일}} 변경을 완료하였습니다.\n"
        "\n"
        "{{단말목록}}\n"
        "\n"
        "업무에 참조 부탁드립니다.\n"
        "감사합니다."
    ),
    vars={"원본제목": "요청 메일 제목", "호칭줄": "'염서연 대리님' 한 줄 + 빈 줄",
          "호칭": "이름 부분 (예전 문구용)", "대수": "단말 수",
          "변경일": "'(2026-09-30)' (날짜가 없으면 비어 있음)",
          "단말목록": "'1. PM95 : 26131A0209, 26128A0418' 모델별 줄(모델을 모르면 'SN: ...')"},
))

_add(Tpl(
    key="wl_reply_date_en", title="WhiteList 등록일 변경 회신 (영문)", group="WhiteList",
    when="등록일만 바꾼 건이고 요청 메일이 영문일 때", to="요청하신 분",
    subject="RE: {{원본제목}}",
    body=(
        "Hello{{호칭}},\n"
        "\n"
        "The registration date of the following {{대수}} device(s) has been changed{{변경일}}.\n"
        "\n"
        "{{단말목록}}\n"
        "\n"
        "Please refer to this for your work.\n"
        "Thank you."
    ),
    vars={"원본제목": "요청 메일 제목", "호칭줄": "'Gil-dong Hong,' 한 줄 + 빈 줄",
          "호칭": "이름 부분", "대수": "단말 수",
          "변경일": "' to 2026-09-30' (날짜가 없으면 비어 있음)",
          "단말목록": "모델별 줄 또는 'SN: ...'"},
))

_add(Tpl(
    key="wl_reply_dqa", title="DQA FOTA 등록 완료 회신", group="WhiteList",
    when="DQA FOTA 작업 ⑥ 회신 단계", to="DQA 요청자 (참조: pm_sqa · pm_ta)",
    subject="RE: {{원본제목}}",
    body=(
        "안녕하세요.\n"
        "\n"
        "{{호칭줄}}"
        "아래 건에 대하여 FOTA 등록을 완료하여 메일 드리니 업무에 참조 부탁 드립니다.\n"
        "등록한 단말은 아래 인용된 요청 내역과 동일합니다.\n"
        "\n"
        "테스트 진행 중 등록이 누락된 단말이 있거나 추가 등록이 필요하시면\n"
        "말씀 부탁 드리겠습니다. 그 밖에 특이사항이 있으셔도 회신 부탁 드립니다.\n"
        "\n"
        "감사합니다."
    ),
    vars={"원본제목": "요청 메일 제목(RE: 는 겹쳐 붙지 않는다)",
          "호칭줄": "'홍길동님.' 한 줄 + 빈 줄 (이름이 없으면 비어 있음)"},
))

_add(Tpl(
    key="wl_new_request", title="WhiteList 신규 요청 접수 알림", group="WhiteList",
    when="포털·화면에서 새 요청이 접수됐을 때", to="관리자 전원",
    subject="[WhiteList] 새 Request 접수 — {{요청번호}}",
    body=(
        "새 WhiteList 등록 요청이 접수되었습니다. 아래 내용을 확인하고 처리해 주세요.\n"
        "\n"
        "■ Request ID : {{요청번호}}\n"
        "■ 요청자     : {{요청자}}\n"
        "■ 팀         : {{팀}}\n"
        "■ 제출일     : {{제출일}}\n"
        "■ 단말기 수  : {{대수}}대\n"
        "■ 유/무상    : {{유무상}}\n"
        "■ 메모       : {{메모}}\n"
        "\n"
        "[단말기 목록]\n"
        "{{단말목록}}\n"
        "\n"
        "▶ 아래 화면에서 승인 또는 거절할 수 있습니다.\n"
        "  {{앱주소}}/whitelist\n"
        "\n"
        "  처리하면 요청자에게 보낼 결과 안내 메일 초안이 함께 만들어집니다.\n"
        "  내용을 확인하고 회신해 주세요.\n"
        "\n"
        + _AUTO_NOTE
    ),
    vars={"요청번호": "요청 id", "요청자": "'홍길동 (gildong.hong)'", "팀": "팀 이름",
          "제출일": "YYYY-MM-DD", "대수": "단말 수", "유무상": "유상/무상",
          "메모": "요청자가 적은 메모", "단말목록": "'1. SN: ... | API: ...' 줄들"},
))

# ── 계정 ─────────────────────────────────────────────────────────────────────
_add(Tpl(
    key="account_welcome", title="계정 생성 안내", group="계정",
    when="관리자가 계정을 만들었을 때", to="새로 만든 계정의 주인",
    subject="[TW Automation] 계정이 생성되었습니다",
    body=(
        "안녕하십니까.\n"
        "TW Automation 계정이 만들어져 안내 드립니다.\n"
        "\n"
        "{{정보표}}\n"
        "처음 로그인하시면 비밀번호 변경 화면이 먼저 열립니다.\n"
        "초기 비밀번호는 계정을 만든 관리자도 알고 있으므로, 반드시 본인만 아는\n"
        "비밀번호로 바꾸신 뒤 이용해 주시기 바랍니다.\n"
        "\n"
        "TW Automation 은 OS 출시 작업, Release Note 제작, FOTA 화이트리스트 등록\n"
        "같은 반복 업무를 돕는 사내 도구입니다. 로그인하시면 담당하신 작업과\n"
        "검수 요청이 화면에 모여 있습니다.\n"
        "\n"
        "계정이나 사용 중 문의는 이 메일에 회신해 주시기 바랍니다.\n"
        "\n"
        + _AUTO_NOTE
    ),
    vars={"정보표": "서비스 주소·아이디·초기 비밀번호를 줄맞춤한 상자"},
))

_add(Tpl(
    key="account_reset", title="비밀번호 초기화 안내", group="계정",
    when="관리자가 비밀번호를 초기화한 직후", to="그 계정의 주인",
    subject="[TW Automation] 비밀번호가 초기화되었습니다",
    body=(
        "안녕하십니까.\n"
        "요청하신 대로 TW Automation 비밀번호를 초기화했습니다.\n"
        "\n"
        "{{정보표}}\n"
        "다음 로그인 때 비밀번호 변경 화면이 먼저 열립니다.\n"
        "위 초기 비밀번호는 임시 값이므로, 본인만 아는 비밀번호로 바꾸신 뒤\n"
        "이용해 주시기 바랍니다.\n"
        "\n"
        "본인이 요청하지 않으셨다면 다른 사람이 초기화를 요청했을 수 있습니다.\n"
        "이 메일에 회신해 바로 알려 주시기 바랍니다.\n"
        "\n"
        + _AUTO_NOTE
    ),
    vars={"정보표": "서비스 주소·아이디·초기 비밀번호를 줄맞춤한 상자"},
))

_add(Tpl(
    key="account_forgot", title="비밀번호 초기화 요청 알림", group="계정",
    when="로그인 화면에서 '비밀번호를 잊으셨나요?' 를 눌렀을 때", to="시스템 관리자",
    subject="[TW Automation][비밀번호 초기화 요청] {{아이디}}",
    body=(
        "로그인 화면에서 비밀번호 초기화 요청이 접수되었습니다.\n"
        "\n"
        "{{정보표}}\n"
        "▶ [계정 관리] 에서 해당 계정의 [비밀번호 초기화] 를 눌러 주시기 바랍니다.\n"
        "  {{계정관리주소}}\n"
        "\n"
        "  초기화하면 초기 비밀번호로 되돌아가고, 본인에게 안내 메일이 자동으로\n"
        "  발송됩니다. 다음 로그인 때 비밀번호 변경 화면이 먼저 열립니다.\n"
        "\n"
        "요청한 사람에게는 계정이 있는지 없는지 알리지 않았습니다.\n"
        "위 '계정 존재' 가 '아니오' 라면 아이디를 떠보는 시도일 수 있으니,\n"
        "초기화하지 마시고 그대로 두시기 바랍니다.\n"
        "\n"
        + _AUTO_NOTE
    ),
    vars={"아이디": "요청에 쓰인 아이디", "정보표": "아이디·요청 시각·계정 존재 여부 상자",
          "계정관리주소": "앱의 [계정 관리] 주소"},
))

_add(Tpl(
    key="portal_reset", title="WhiteList 포털 비밀번호 재설정", group="계정",
    when="포털 사용자가 비밀번호 재설정을 요청했을 때", to="포털 사용자",
    subject="[Point Mobile] WhiteList 비밀번호 재설정",
    body=(
        "{{이름}} 님, 안녕하세요.\n"
        "\n"
        "WhiteList 처리 내역 화면의 비밀번호 재설정을 요청하셨습니다.\n"
        "아래 주소에서 새 비밀번호를 정해 주세요.\n"
        "\n"
        "  {{재설정주소}}\n"
        "\n"
        "이 링크는 {{유효시간}}분 동안만 쓸 수 있습니다. 시간이 지났으면 화면에서\n"
        "재설정을 다시 요청해 주시기 바랍니다.\n"
        "\n"
        "요청하신 적이 없다면 이 메일을 무시하셔도 됩니다. 이 링크를 열지 않는 한\n"
        "비밀번호는 바뀌지 않습니다. 다만 모르는 요청이 반복된다면 아래 주소로\n"
        "알려 주시기 바랍니다.\n"
        "  {{문의주소}}\n"
        "\n"
        "감사합니다."
    ),
    vars={"이름": "포털 계정 이름", "재설정주소": "1회용 재설정 링크",
          "유효시간": "링크 유효 시간(분)"},
))

# ── 자동 알림 ────────────────────────────────────────────────────────────────
_add(Tpl(
    key="notice_whitelist", title="WhiteList 미처리 알림", group="자동 알림",
    when="매일 11시·16시, 미처리 요청이 있을 때만", to="알림 대상 역할의 사용자",
    subject="[TW Automation] WhiteList 등록 요청 {{건수}}건 대기",
    body=(
        "FOTA WhiteList 등록 요청이 {{건수}}건 대기 중입니다.\n"
        "아래 목록을 확인하고 처리해 주세요.\n"
        "\n"
        "  {{화면주소}}\n"
        "\n"
        "------------------------------------------------------------\n"
        "{{목록}}"
        "------------------------------------------------------------\n"
        "\n"
        "화면에서 요청을 열면 등록·거절 처리와 요청자 회신을 함께 할 수 있습니다.\n"
        "이미 처리하신 건은 이 목록에서 빠지며, 남은 건이 없으면 알림도 오지 않습니다.\n"
        "\n"
        + _AUTO_NOTE + " (매일 11시·16시)"
    ),
    vars={"건수": "대기 건수", "화면주소": "[WhiteList] 화면 주소",
          "목록": "요청별 제목·발신·S/N 줄들"},
))

_add(Tpl(
    key="notice_osupdate", title="OS Update 미착수 회차 알림", group="자동 알림",
    when="매일 11시·16시, 시작하지 않은 회차가 있을 때만", to="알림 대상 역할의 사용자",
    subject="[TW Automation] 시작하지 않은 OS Update 회차 {{건수}}건",
    body=(
        "아직 시작하지 않은 OS Update 회차가 {{건수}}건 있습니다.\n"
        "아래 화면에서 작업을 시작할 수 있습니다.\n"
        "\n"
        "  {{화면주소}}\n"
        "\n"
        "------------------------------------------------------------\n"
        "{{목록}}"
        "------------------------------------------------------------\n"
        "\n"
        "※ '배포 공지만 있음' 은 트리거 메일([FIH])이 오지 않은 회차입니다 —\n"
        "  비GMS·긴급 배포 등이 여기에 해당합니다. 이 회차는 버전을 직접 지정해\n"
        "  시작하셔야 합니다.\n"
        "\n"
        "한 번 알린 회차는 다시 알리지 않습니다. 시작하지 않을 회차라면 그대로\n"
        "두셔도 됩니다.\n"
        "\n"
        + _AUTO_NOTE + " (매일 11시·16시)"
    ),
    vars={"건수": "회차 수", "화면주소": "[OS Update] 화면 주소",
          "목록": "제품·버전·상태 줄들"},
))

_add(Tpl(
    key="sb_review", title="Solution Buffer 검토 요청", group="자동 알림",
    when="Redmine 이슈가 Resolved 되어 초안을 만들었을 때", to="이슈 담당자 (없으면 관리자)",
    subject="[TW Automation] SB 업로드 문서 검토 요청 — #{{이슈번호}} {{문서제목}}",
    body=(
        "{{담당자}} 님,\n"
        "\n"
        "담당하신 Redmine 이슈 #{{이슈번호}} 가 Resolved 되어, Solution Buffer 에\n"
        "올릴 문서 초안을 자동으로 작성했습니다.\n"
        "\n"
        "{{정보표}}{{대신보냄}}\n"
        "────────────────────────────────────────────────────────\n"
        "아래 주소로 로그인하시면 [Solution Buffer] 탭에 본인 담당 문서만 모여 있습니다.\n"
        "  {{화면주소}}\n"
        "\n"
        "  1. 제목·공개 등급·본문을 확인하고 필요하면 고칩니다.\n"
        "     (등급을 바꾸면 Confluence 템플릿과 라벨도 따라 바뀝니다)\n"
        "  2. [미리보기·검증] 으로 실제 올라갈 모습을 확인합니다.\n"
        "  3. [업로드] 를 누르면 그때 Confluence 에 반영됩니다.\n"
        "\n"
        "누르지 않으면 초안으로만 남고 Confluence 에는 아무것도 올라가지 않습니다.\n"
        "올릴 필요가 없는 내용이면 [폐기] 로 정리해 주세요.\n"
        "────────────────────────────────────────────────────────\n"
        "\n"
        "초안은 이슈 내용을 바탕으로 만든 것이라 사실관계와 표현을 꼭 확인해\n"
        "주셔야 합니다. 고객에게 공개되는 문서입니다.\n"
        "\n"
        + _AUTO_NOTE
    ),
    vars={"담당자": "이슈 담당자 이름", "이슈번호": "Redmine 이슈 번호",
          "문서제목": "만든 문서 제목",
          "정보표": "이슈·문서 제목·추천 등급·Applies to (개정판이면 개정 줄까지)",
          "대신보냄": "담당자 주소를 못 찾아 관리자에게 보낼 때의 안내(아니면 비어 있음)",
          "화면주소": "[Solution Buffer] 화면 주소"},
))

# ── 검수 ─────────────────────────────────────────────────────────────────────
_add(Tpl(
    key="review_request", title="검수 요청", group="검수",
    when="[검수 요청] 을 눌렀을 때", to="해당 검수자 역할 전원",
    subject="[TW Automation][{{요청종류}} 요청] {{제품}} {{버전}} {{대상}}",
    body=(
        "안녕하세요.\n"
        "\n"
        "{{요청자}} 님이 {{대상}} {{요청종류}}{{조사}} 요청했습니다.\n"
        "\n"
        "■ 제품/버전 : {{제품}} {{버전}}\n"
        "■ 요청자    : {{요청자}}\n"
        "■ 요청일    : {{오늘}}\n"
        "\n"
        "{{안내}}\n"
        "\n"
        "확인하신 결과는 화면에서 승인 또는 반려로 남겨 주시면 요청자에게 자동으로\n"
        "안내됩니다. 수정이 필요한 부분은 반려 사유에 적어 주시면 그대로 전달됩니다.\n"
        "\n"
        + _AUTO_NOTE
    ),
    vars={"요청종류": "'검수' · 'RN 승인' 등", "제품": "제품명", "버전": "버전",
          "대상": "무엇을 검수하는지", "요청자": "요청한 사람", "조사": "을/를",
          "안내": "무엇을 눌러야 하는지 한 줄"},
))

_add(Tpl(
    key="review_result", title="검수 결과 (승인·반려)", group="검수",
    when="검수자가 승인·반려했을 때", to="요청한 사람",
    subject="[TW Automation][{{요청종류}} {{결과}}] {{제품}} {{버전}} {{대상}}",
    body=(
        "안녕하세요.\n"
        "\n"
        "{{첫줄}}\n"
        "\n"
        "■ 제품/버전 : {{제품}} {{버전}}\n"
        "■ 처리자    : {{처리자}}\n"
        "■ 처리일    : {{오늘}}\n"
        "■ 메모      : {{메모}}\n"
        "\n"
        "내용은 앱에서 다시 확인하실 수 있습니다.\n"
        "  {{앱주소}}\n"
        "\n"
        "반려된 건이라면 위 메모를 확인해 수정하신 뒤 다시 요청해 주시기 바랍니다.\n"
        "\n"
        + _AUTO_NOTE
    ),
    vars={"요청종류": "'검수' 등", "결과": "'승인' 또는 '반려'", "제품": "제품명",
          "버전": "버전", "대상": "무엇을 검수했는지",
          "첫줄": "'요청하신 … 건이 승인되었습니다.'", "처리자": "처리한 사람",
          "메모": "처리자가 적은 메모"},
))

_add(Tpl(
    key="mid_review_result", title="RN 승인 결과 회신", group="검수",
    when="중간 검수에서 RN 을 승인·반려했을 때", to="작성자 (참조: 다른 RN 승인자)",
    subject="Re: [TW Automation] {{제품}} {{버전}} Release Note RN 승인 {{결과}}",
    body=(
        "안녕하세요.\n"
        "\n"
        "{{제품}} {{버전}} Release Note 가 {{결과}}되었습니다.\n"
        "\n"
        "■ 처리자 : {{처리자}}\n"
        "■ 처리일 : {{오늘}}\n"
        "■ {{메모이름}}   : {{메모}}\n"
        "\n"
        "{{다음안내}}\n"
        "\n"
        "진행 상황은 앱에서 확인하실 수 있습니다.\n"
        "  {{앱주소}}\n"
        "\n"
        + _AUTO_NOTE
    ),
    vars={"제품": "제품명", "버전": "버전", "결과": "'승인' 또는 '반려'",
          "처리자": "처리한 사람", "메모이름": "'메모'(승인) 또는 '반려 사유'(반려)",
          "메모": "적은 내용", "다음안내": "다음에 무엇이 일어나는지 안내"},
))

# ── License ──────────────────────────────────────────────────────────────────
_add(Tpl(
    key="license_request", title="License 승인 요청", group="License",
    when="PMDM License 발급을 신청했을 때", to="최종 승인자 전원",
    subject="[TW Automation][License 승인 요청] {{회사}} ({{문서번호}})",
    body=(
        "안녕하세요.\n"
        "\n"
        "{{신청자}} 님이 PMDM License 발급을 신청했습니다. 승인 또는 반려를\n"
        "처리해 주시기 바랍니다.\n"
        "\n"
        "■ Doc ID   : {{문서번호}}\n"
        "■ Company  : {{회사}}\n"
        "■ Quantity : {{수량}}\n"
        "■ Contact  : {{담당자}}\n"
        "■ 아이디   : {{라이선스아이디}}\n"
        "■ 신청일   : {{오늘}}\n"
        "■ 비고     : {{비고}}\n"
        "\n"
        "▶ 아래 검수 화면에서 발급될 License Certificate 를 확인하고 승인 · 반려하실 수 있습니다.\n"
        "  {{검수주소}}\n"
        "\n"
        "  승인하시면 신청자에게 라이선스 인증서 PDF 가 자동으로 발송됩니다.\n"
        "  반려하실 때는 사유를 적어 주시면 신청자에게 그대로 전달됩니다.\n"
        "\n"
        + _AUTO_NOTE
    ),
    vars={"신청자": "신청한 사람", "문서번호": "Doc ID", "회사": "고객사",
          "수량": "수량", "담당자": "'홍길동 (mail@example.com)'",
          "라이선스아이디": "License user id", "비고": "신청자가 적은 비고",
          "검수주소": "이 신청의 검수 화면 — 검수 웹(tw-automation-confirm)이 있으면 그 주소"},
))

_add(Tpl(
    key="license_approval", title="License 승인 통보", group="License",
    when="관리자가 승인했을 때", to="신청자",
    subject="[PMDM License 승인] {{회사}} ({{문서번호}})",
    body=(
        "안녕하세요.\n"
        "\n"
        "요청하신 PMDM License Key 발급이 승인되었습니다.\n"
        "{{첨부안내}}\n"
        "\n"
        "■ Doc ID      : {{문서번호}}\n"
        "■ Company     : {{회사}}\n"
        "■ Quantity    : {{수량}}\n"
        "■ 승인자      : {{승인자}}\n"
        "■ 승인일      : {{오늘}}\n"
        "■ 메모        : {{메모}}\n"
        "\n"
        "License Key 는 첨부된 인증서에만 들어 있습니다. 메일 본문에는 넣지 않으니\n"
        "첨부 파일을 안전하게 보관해 주시기 바랍니다.\n"
        "\n"
        "발급 내용에 수정이 필요하거나 문의가 있으시면 이 메일로 회신해 주시기\n"
        "바랍니다.\n"
        "\n"
        "감사합니다."
    ),
    vars={"문서번호": "Doc ID", "회사": "고객사", "수량": "수량", "승인자": "승인한 사람",
          "메모": "관리자 메모",
          "첨부안내": "인증서 PDF 첨부 여부에 따른 한 줄"},
))

_add(Tpl(
    key="license_rejection", title="License 반려 통보", group="License",
    when="관리자가 반려했을 때", to="신청자",
    subject="[PMDM License 반려] {{회사}} ({{문서번호}})",
    body=(
        "안녕하세요.\n"
        "\n"
        "요청하신 PMDM License Key 발급이 아래 사유로 반려되었습니다.\n"
        "\n"
        "■ Doc ID      : {{문서번호}}\n"
        "■ Company     : {{회사}}\n"
        "■ 반려자      : {{반려자}}\n"
        "■ 반려일      : {{오늘}}\n"
        "■ 반려 사유   : {{사유}}\n"
        "\n"
        "사유를 확인하신 뒤 내용을 보완해 다시 신청해 주시기 바랍니다.\n"
        "사유가 명확하지 않거나 협의가 필요하시면 이 메일로 회신해 주시기 바랍니다.\n"
        "\n"
        "감사합니다."
    ),
    vars={"문서번호": "Doc ID", "회사": "고객사", "반려자": "반려한 사람",
          "사유": "반려 사유"},
))


# ── 저장·읽기 ────────────────────────────────────────────────────────────────
def groups() -> list[str]:
    out: list[str] = []
    for t in TEMPLATES.values():
        if t.group not in out:
            out.append(t.group)
    return out


def get(key: str) -> dict:
    """{key, title, …, subject, body, edited} — 고친 것이 있으면 그것."""
    t = TEMPLATES.get(key)
    if t is None:
        return {}
    subject, body, edited, by, at = t.subject, t.body, False, "", None
    with get_session() as db:
        row = db.query(MailTemplate).filter_by(key=key).first()
        if row is not None:
            subject, body, edited = row.subject, row.body, True
            by, at = row.updated_by, row.updated_at
    return {"key": key, "title": t.title, "group": t.group, "when": t.when, "to": t.to,
            "subject": subject, "body": body, "vars": dict(t.vars), "auto": dict(AUTO),
            "default_subject": t.subject, "default_body": t.body,
            "edited": edited, "updated_by": by, "updated_at": at}


def save(key: str, subject: str, body: str, actor: str = "") -> tuple[bool, str]:
    if key not in TEMPLATES:
        return False, "없는 문구입니다."
    if not (subject or "").strip():
        return False, "제목을 비워 둘 수 없습니다."
    if not (body or "").strip():
        return False, "본문을 비워 둘 수 없습니다."
    with get_session() as db:
        row = db.query(MailTemplate).filter_by(key=key).first()
        if row is None:
            row = MailTemplate(key=key)
            db.add(row)
        row.subject, row.body, row.updated_by = subject, body, actor
        db.commit()
    return True, "저장했습니다."


def reset(key: str) -> tuple[bool, str]:
    """고친 것을 지워 기본 문구로 되돌린다."""
    with get_session() as db:
        row = db.query(MailTemplate).filter_by(key=key).first()
        if row is None:
            return True, "이미 기본 문구입니다."
        db.delete(row)
        db.commit()
    return True, "기본 문구로 되돌렸습니다."


def _known(key: str) -> set:
    t = TEMPLATES.get(key)
    return set((t.vars if t else {}).keys()) | set(AUTO.keys())


def unknown_vars(key: str, subject: str, body: str) -> list[str]:
    """문구에 쓰였지만 값이 채워지지 않는 이름들. 저장 전에 짚어 주기 위한 것."""
    used = set(_VAR.findall(subject or "")) | set(_VAR.findall(body or ""))
    return sorted(used - _known(key))


def missing_vars(key: str, subject: str, body: str) -> list[str]:
    """이 문구만의 값인데 문구에서 빠진 이름들. 지워도 되지만 알려는 준다.

    어디서나 쓸 수 있는 값(AUTO)은 안 써도 이상할 것이 없으므로 세지 않는다.
    """
    t = TEMPLATES.get(key)
    used = set(_VAR.findall(subject or "")) | set(_VAR.findall(body or ""))
    return sorted(set((t.vars if t else {}).keys()) - used)


def fill(text: str, values: dict) -> str:
    """{{이름}} 을 값으로 바꾼다. 모르는 이름은 **그대로 둔다**."""
    def sub(m):
        name = m.group(1)
        return str(values[name]) if name in values else m.group(0)

    return _VAR.sub(sub, text or "")


def render(key: str, **values) -> tuple[str, str]:
    """(제목, 본문). 고친 문구가 있으면 그것으로 만든다."""
    cur = get(key)
    if not cur:
        return "", ""
    vals = dict(auto_values())
    vals.update(values)                      # 부르는 쪽 값이 우선
    return fill(cur["subject"], vals), fill(cur["body"], vals)


def sample(key: str) -> dict:
    """미리보기용 예시 값 — 설명에서 따옴표 안의 예시를 뽑아 쓰고, 없으면 이름을 쓴다."""
    t = TEMPLATES.get(key)
    out: dict[str, str] = {}
    for name, desc in list((t.vars if t else {}).items()) + list(AUTO.items()):
        m = re.search(r"'([^']*)'", desc or "")
        out[name] = m.group(1) if m else f"({name})"
    out.update(auto_values())                # 앱주소·문의주소·오늘은 실제 값으로
    return out
