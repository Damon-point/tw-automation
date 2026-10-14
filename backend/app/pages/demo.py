"""시연용 화면 (/demo) — 실제 화면·실행 과정·실행 결과를 그대로 옮겨 온 데모.

화면 구성, 진행 중에 뜨는 문구, 끝났을 때 나오는 결과 텍스트를 실제 파이프라인에서
그대로 가져왔다. 진행 문구는 app/pipeline/*.py 의 progress.step() 문자열이고, 결과
텍스트는 run_rn_research 등이 돌려주는 StageOutput.result_text 의 형식 그대로다.

바깥으로 나가는 길은 전부 끊었다:
  · DB(tw_app·whitelist·pmdm_license·issuetrack)·상태 파일·파일서버·Redmine·FOTA·
    Confluence·Service Portal·Dropbox·Claude·메일 — 하나도 호출하지 않는다
  · 모든 데이터는 이 모듈 안의 딕셔너리다(새로고침하면 처음 상태로 돌아온다)
  · 저장·발송·업로드 버튼은 화면만 바꾼다

진짜 데이터가 없으므로 로그인 없이 연다(app.auth.UNRESTRICTED_PAGES).
"""
from __future__ import annotations

from copy import deepcopy

from nicegui import app as ng_app, ui

from app.theme import apply_theme, page_header

_REDMINE = "https://issue.pointmobile.co.kr/issues/"
_WORK = "C:\\Users\\POINTMOBILE\\dev\\tw-automation\\work\\rn_research\\job-18_20260819-094512"

# ── 실행 결과 텍스트 (실제 StageOutput.result_text 형식) ────────────────────
_RESULT_RN = """[RN 리서치 · PM95 A15]
버전: 95.52
근거요약: 배포 메일 합산(#804 95.52 + #622 95.52 + #372 95.52 B3 + #274 95.52 B3)

[근거]
· 버전 판별: 출시본 95.51(Mar 31 2026, Service Portal) + FIH 승인일 → 95.52
· 참조 메일(개선사항 출처): #804 "[PM95 A15][GMS MR] 95.52 (Jul 20 2026) RC 배포" (Jul 20 2026)
· 확인한 개선사항 이슈 40건 (상한 120): #33779, #33775, #32907, #32723, #25999, #29238,
  #30479, #31054, #31402, #31776 … 외 30건
· 생성자/해결자 추적을 위해 관계 이슈 12건 추가 확인
· 공개 RN 선별: 포함(Y) 8건 / 제외 32건 (결함 수정은 'stability'로 뭉침)
  ※ 기본값은 전부 반영(Y) 입니다 — 빼실 항목은 선별표에서 미반영으로 바꾸세요
    (각 행 비고에 판정 사유가 남아 있습니다).
· 입력 항목: 직전 버전 RN(PM95_Android15_Release_Note_95.51.pdf) 에서 가져옴 — 13개
  (Known Issues, GMS Package, SDK, Audio, Touch, Wi-Fi …) · 실행 전 입력값·RN 승인 입력이
  있으면 그쪽이 우선

개선사항 이슈 40건 → Redmine 상세 40건 → 변경 40건 → 공개 항목 8건.
RN(docx): {work}\\PM95_A15_Release_Note_95.52 for Sales.docx
전체 이력 RN: {work}\\PM95_A15_Release_Note_95.52.docx (판 2개)
※ 공개 RN 선별표(이슈·포함·공개문구)는 '검수하기' 팝업에 표로 표시됩니다.
Revision History: 자동 큐레이션(공개 선별) · 9줄 (버전표기 95.52 (Jul 20 2026))
※ Revision History 는 '검수하기' 팝업에서 편집 후 RN 재생성할 수 있습니다.
컴포넌트 버전(Audio/Touch/Wi-Fi/Known Issues)은 실행 전 입력값을 사용합니다.""".replace(
    "{work}", _WORK)

_RESULT_OS = """[OS Download · PM95 A15]
정식 버전: 95.52  (출시본 95.51 + FIH 승인일 → 95.52)
파일서버 경로: \\\\192.168.123.104\\Project\\SQA\\OS\\PM95\\Android15\\95.52

내려받은 파일 4종
· OS(sdcard) : PM95_A15_95.52.zip                    2.14 GB
· 플래싱 툴  : PM95_A15_95.52_FlashTool.zip           186 MB
· EmKit SDK  : PointMobile_SDK_3.41.09.zip             42 MB
· 체크섬     : PM95_A15_95.52.md5                     1 KB

sdcard zip 재구성: 14개 파일 → PM95_A15_95.52_sdcard.zip (2.14 GB)
빌드 지문: PointMobile/PM95/PM95:15/AP3A.240905.015/95.52:user/release-keys
※ 보안 패치 수준·SDK 버전은 RN 승인 화면에서 사람이 입력합니다(여기서 추출하지 않습니다)."""

_RESULT_DROPBOX = """[Dropbox 업로드]
업로드 1건 → 등록된 폴더 4곳에 복사했습니다.

· OS(sdcard)  PM95_A15_95.52_sdcard.zip   2.14 GB   → 4개 폴더
· 플래싱 툴   PM95_A15_95.52_FlashTool.zip  186 MB   → 4개 폴더
· EmKit SDK   PointMobile_SDK_3.41.09.zip    42 MB   → 4개 폴더
· Release Note PM95_A15_Release_Note_95.52 for Sales.pdf  → 4개 폴더

공유 링크
· https://www.dropbox.com/scl/fo/…/PM95/Android15/95.52
※ 구버전 정리는 마지막 단계에서 따로 합니다 — 여기서는 아무것도 지우지 않습니다."""

_RESULT_FOTA = """[FOTA 업로드]
Release_Note 시트 · OS 등록표를 만들었습니다.

· 회차 번호: 6th   (같은 회차의 C 리비전은 같은 번호를 씁니다)
· 대상 모델: PM95 (Android 15)
· 등록 버전: 95.52   빌드일 2026-07-20
· 공개 문구 8건을 Release_Note 시트에 채웠습니다.

산출물: PM95_A15_FOTA_95.52.xlsx
※ 이 단계는 엑셀만 만듭니다. FOTA 서버 반영은 사람이 업로드해야 일어납니다."""

_RESULT_PORTAL = """[Service Portal 편집·업로드]
로그인 → 최근 Resources 문서를 원본으로 복사해 새 문서를 만들었습니다.

· 문서 #14827  PM95 Android 15 — Resources
  - Latest OS version   95.51 (Mar 31 2026)  →  95.52 (Jul 20 2026)
  - Security patch      June 5, 2026         →  July 5, 2026
  - Release Note PDF    95.51 판              →  95.52 판 (재업로드)
· 문서 #14828  4C(플래싱) — 플래싱 툴 zip 교체

※ 실제 게시는 '검수하기'에서 확인 후 [검수 완료]를 눌러야 일어납니다."""

_RESULT_KB = """[Knowledge Base 편집]
Confluence Release Notes 페이지를 읽어 이번 회차 항목을 분류했습니다.

분류 결과 (VoC/EmKit/OS × NEW/IMPROVED/FIXED)
· NEW       2건   Scanner HW 키 모드 · WLAN 로밍 옵션
· IMPROVED  3건   Battery 안전 · Scanner 프로파일 · 로그 백업
· FIXED     3건   (개별 항목화하지 않고 stability 한 줄로 묶음)

미리보기를 렌더했습니다 — 반영 전 화면에서 확인하실 수 있습니다.
※ 이 단계는 편집안만 만듭니다. 페이지 반영은 [검수 완료] 시점에 일어납니다."""

_RESULT_NEWS = """[Newsletter 제작]
공개 항목 8건으로 KR/EN 초안을 만들었습니다(발송하지 않았습니다).

· 제목(KR)  PM95 Android 15 — 95.52 업데이트 안내
· 제목(EN)  PM95 Android 15 — 95.52 Update
· 카드 5장  Scanner · WLAN · Battery · Security patch · 기타 개선
· 근거      Release Note 공개 선별 8건 + 보안패치 July 5, 2026

산출물: newsletter_kr.html · newsletter_en.html
※ Mailchimp 발송은 '검수하기'에서 사람이 확인한 뒤 별도로 진행합니다."""

_RESULT_CLEANUP = """[Dropbox 정리 · 구버전 확인]
등록된 Dropbox 경로 4개에서 구버전 파일 11개를 찾았습니다.
삭제는 '검수하기' 팝업에서 경로별로 직접 실행하세요.

· 배포용(고객)      구버전 3개  (/PointMobile/OS/PM95/Android15)
· 파트너            구버전 3개  (/Partner/OS/PM95)
· 사내 공유         구버전 3개  (/Internal/OS/PM95)
· 아카이브          구버전 2개  (/Archive/PM95)

※ 이 단계는 아무것도 지우지 않습니다 — 무엇이 남아 있는지만 보여줍니다."""


# ── 단계 정의: 실제 진행 문구(progress.step) 그대로 ────────────────────────
_STAGES = [
    {"key": "os_download", "label": "OS Download", "status": "done", "ai": False,
     "by": "TA", "result": _RESULT_OS,
     "steps": ["제품·Android 파악 중…", "정식 버전 판별 중…",
               "파일서버에서 OS 폴더 탐색 중…",
               "다운로드 중… PM95_A15_95.52.zip (2.14 GB)",
               "다운로드 중… PM95_A15_95.52_FlashTool.zip (186 MB)",
               "sdcard zip 생성 중… OS (14개 파일)"]},
    {"key": "release_note", "label": "Release Note 제작", "status": "done", "ai": True,
     "by": "TA", "result": _RESULT_RN,
     "steps": ["트리거 메일 분석 중 (제품·Android)…",
               "배포 메일에서 개선사항 이슈 목록 찾는 중…",
               "파일서버에 이미 만든 RN 이 있는지 확인 중…",
               "Redmine 이슈 상세 읽는 중… (40건)",
               "관계 이슈 추가 읽는 중… (12건)",
               "Claude 이슈 요약 중… (6/40건)", "Claude 이슈 요약 중… (24/40건)",
               "Claude 이슈 요약 중… (40/40건)",
               "공개 Release Note 항목 선별 중…",
               "Claude 이슈 판정 중… (8/40건)", "Claude 이슈 판정 중… (40/40건)",
               "직전 버전 RN 에서 입력 항목·이력 가져오는 중…",
               "Revision History 정리(큐레이션) 중…",
               "Release Note 문서(docx) 생성 중…",
               "전체 이력 Release Note 만드는 중…"]},
    {"key": "dropbox_release", "label": "Dropbox 업로드", "status": "done", "ai": False,
     "by": "TA", "result": _RESULT_DROPBOX,
     "steps": ["sdcard zip 업로드 중… OS 2.14 GB",
               "Dropbox 업로드+복사 중… OS PM95_A15_95.52_sdcard.zip → 4개 폴더",
               "플래싱 툴(zip) 업로드 중…", "EmKit SDK zip 업로드 중…",
               "Release Note PDF 업로드 중…"]},
    {"key": "excel_docs", "label": "FOTA 업로드", "status": "done", "ai": False,
     "by": "TA", "result": _RESULT_FOTA,
     "steps": ["회차 번호 계산 중…", "Release_Note 시트 채우는 중…",
               "엑셀 서식 적용 중…"]},
    {"key": "service_portal", "label": "Service Portal 편집·업로드", "status": "done",
     "ai": True, "by": "", "result": _RESULT_PORTAL,
     "steps": ["Service Portal 로그인·문서 목록 조회 중…",
               "Service Portal 복사 원본(최근 Resources 문서) 조회 중…",
               "Service Portal 문서 편집 중… (#신규, Resources)",
               "Service Portal 4C(플래싱) 편집 중…",
               "Release Note PDF 업로드 중…"]},
    {"key": "release_note_edit", "label": "Knowledge Base 편집", "status": "done",
     "ai": True, "by": "", "result": _RESULT_KB,
     "steps": ["Release Notes(Confluence) 로그인·페이지 읽는 중…",
               "Knowledge Base 섹션·항목 분류(VoC/EmKit/OS… × NEW/IMPROVED/FIXED) 중…",
               "Knowledge Base 미리보기 렌더 중…"]},
    {"key": "newsletter", "label": "Newsletter 제작", "status": "done", "ai": True,
     "by": "TA", "result": _RESULT_NEWS,
     "steps": ["Claude가 뉴스레터 카드 작성 중… (공개 항목 8건)",
               "KR/EN 뉴스레터 HTML 생성 중…"]},
    {"key": "dropbox_cleanup", "label": "Dropbox 정리 (구버전 삭제)", "status": "done",
     "ai": False, "by": "", "result": _RESULT_CLEANUP,
     "steps": ["등록된 Dropbox 경로 4곳 조회 중…", "구버전 파일 추리는 중…"]},
]


#: 단계 정의를 키로 찾기 — 아래 두 갈래가 같은 정의를 나눠 쓴다.
_STAGE_DEF = {s["key"]: s for s in _STAGES}

#: 실제 시스템의 갈래별 단계(app.stages.SUBMISSION_STAGE_KEYS / APPROVAL_STAGE_KEYS).
#: ② Release Note 제작은 Submission 에서만 한다 — Approval 은 그 산출물을 올리기만 한다.
_SIDE_KEYS = {
    "submission": ["release_note", "release_note_edit", "newsletter"],
    "approval": ["os_download", "dropbox_release", "excel_docs", "service_portal",
                 "release_note_edit", "newsletter", "dropbox_cleanup"],
}

#: Approval 에서는 이름이 달라진다 — 제작은 Submission 에서 끝났고 여기선 올리고 보낸다.
_APPROVAL_LABEL = {"release_note_edit": "Knowledge Base 업로드",
                   "newsletter": "Newsletter 발송"}

#: Approval 의 **아직 안 끝난** 단계 — 시연을 여기서 이어서 보여 준다
#: (사용자 확정 2026-08-28). 나머지는 끝난 상태로 둔다.
#:   · Newsletter 발송   = 검수 대기 → [검수하기] 를 눌러 보낼 내용을 확인한다
#:   · Dropbox 정리      = 대기      → [실행 승인] 으로 시작하는 모습을 보여 준다
_APPROVAL_OPEN = {"newsletter": "review", "dropbox_cleanup": "pending"}

_RESULT_KB_UPLOAD = """[Knowledge Base 업로드]
Submission 승인본을 그대로 올렸습니다 (다시 만들지 않습니다).
대상 페이지: PM95 Android 15 — Release Notes
추가된 블록: 95.52 (Jul 20 2026)
이전 최신 블록(95.51)의 OS Download Link 는 규칙대로 걷어냈습니다."""

_RESULT_NEWS_SEND = """[Newsletter 발송]
Submission 승인본을 그대로 발송했습니다 (다시 만들지 않습니다).
대상 태그: PM95-A15-KR (312명) · PM95-A15-EN (688명)
발송 결과: 성공 1,000 · 실패 0
※ 되돌릴 수 없는 동작이라 확인창에서 대상과 수신자 수를 확인한 뒤 실행합니다."""

_APPROVAL_STEPS = {
    "release_note_edit": ["Submission 승인본 불러오는 중…",
                          "Knowledge Base 페이지 여는 중…",
                          "새 버전 블록 삽입 중…"],
    "newsletter": ["Submission 승인본 불러오는 중…",
                   "대상 태그 수신자 조회 중… (1,000명)",
                   "발송 중… (KR 312 / EN 688)"],
}

#: 이 회차를 '신규'로 판단한 근거 메일 — 실제 화면의 '근거 메일 N건' 과 같다.
#: 본문은 시연용으로 지은 것이다(실제 메일 원문이 아니다). 사람 이름은 쓰지 않는다 —
#: /demo 는 로그인 없이 열리는 화면이다.
_TRIGGER_MAILS = [
    {"subject": "[FIH][Submission] POINTMOBILE PM95 has been submitted to Google",
     "when": "2026-07-16 16:51", "original": True,
     "sender": "FIH GMS Certification <gms-cert@fih.example.com>",
     "body": "Dear Partner,\n\n"
             "The build below has been submitted to Google for GMS approval.\n\n"
             "  Model        : POINTMOBILE PM95\n"
             "  Android      : 15\n"
             "  Build        : PM95_A15_95.52\n"
             "  Submitted    : Jul 16, 2026 16:51 (KST)\n\n"
             "You will receive a separate notification once the shipment is granted.\n\n"
             "Best regards,\nFIH GMS Certification"},
    {"subject": "[FIH][Approval] The shipment of POINTMOBILE PM95 has been granted "
                "by Google",
     "when": "2026-07-16 18:22", "original": True,
     "sender": "FIH GMS Certification <gms-cert@fih.example.com>",
     "body": "Dear Partner,\n\n"
             "Google has granted the shipment approval for the build below.\n\n"
             "  Model        : POINTMOBILE PM95\n"
             "  Android      : 15\n"
             "  Build        : PM95_A15_95.52\n"
             "  Approved     : Jul 16, 2026 18:22 (KST)\n"
             "  GMS Package  : 15_202606_R1\n\n"
             "You may proceed with the release.\n\n"
             "Best regards,\nFIH GMS Certification"},
    {"subject": "[PM95][GMS MR] 95.52 (Jul 18 2026) 배포",
     "when": "2026-07-18 14:05", "original": True,
     "sender": "TA <pm_ta@pointmobile.com>",
     "body": "안녕하세요, TA 팀입니다.\n\n"
             "PM95 (Android 15) GMS MR 배포 안내드립니다.\n\n"
             "  · OS 버전    : 95.52 (Jul 18 2026)\n"
             "  · 빌드 일자  : 2026-07-18\n"
             "  · 보안 패치  : July 5, 2026\n"
             "  · SDK        : 3.41.09\n"
             "  · 파일 위치  : \\\\192.168.123.104\\Project\\SQA\\OS\\PM95\\Android15\\95.52\n\n"
             "이번 배포에 포함된 개선 사항은 Redmine salesissue 프로젝트에 등록된\n"
             "항목을 참고해 주시기 바랍니다.\n\n"
             "감사합니다.\nTA"},
]
_TRIGGER_REPLIES = 3

_STATUS = {
    "pending": ("대기", "grey-6"),
    "running": ("실행 중", "blue-7"),
    "review": ("검수 대기", "orange-8"),
    "done": ("완료", "green-8"),
}

_PRODUCTS = [
    {"product": "PM95", "android": "15", "type": "GMS", "cur": "95.52", "date": "2026-07-20",
     "state": "진행 중"},
    {"product": "PM95", "android": "13", "type": "GMS", "cur": "95.07C1", "date": "2026-07-10",
     "state": "완료"},
    {"product": "PM84P", "android": "15", "type": "GMS", "cur": "PM84PG15.03",
     "date": "2026-07-27", "state": "완료"},
    {"product": "PM86", "android": "11", "type": "GMS", "cur": "86.07", "date": "2026-06-19",
     "state": "완료"},
    {"product": "PM45", "android": "9", "type": "GMS", "cur": "45.81", "date": "2026-07-19",
     "state": "진행 중"},
    {"product": "MF52E", "android": "15", "type": "GMS", "cur": "MF52EG15.00",
     "date": "2026-08-12", "state": "대기"},
]

_TASKS = [
    {"job": "job-18", "product": "PM95", "android": "15", "version": "95.52",
     "side": "Approval", "done": 3, "total": 8, "next": "Release Note 제작",
     "trigger": "[FIH][Approval] PM95 A15 95.52 GMS approval", "when": "2026-07-21 09:12"},
    {"job": "job-15", "product": "PM45", "android": "9", "version": "45.81",
     "side": "Submission", "done": 1, "total": 3, "next": "Release Note 제작",
     "trigger": "[FIH][Submission] PM45 A9 45.81 GMS submission", "when": "2026-07-14 14:40"},
    {"job": "job-25", "product": "MF52E", "android": "15", "version": "MF52EG15.00",
     "side": "Submission", "done": 0, "total": 3, "next": "Release Note 제작",
     "trigger": "(트리거 메일 없음 — /osupdate 에서 직접 시작)", "when": "2026-08-12 10:05"},
]

_SELECT_ROWS = [
    {"no": 1, "issue": "[VoC] 스캐너 하드웨어 키 모드 추가", "team": "SW1", "customer": "",
     "gubun": "VOC", "include": "Y", "reason": "신규 기능 (PM95/PM67 한정)",
     "redmine": "33779", "sensitive": False,
     "public": "Added the HW Scan Key Mode.",
     "apply_note": "[Scanner] Added the HW Scan Key Mode."},
    {"no": 2, "issue": "WLAN 로밍 기본값 변경", "team": "SW2", "customer": "",
     "gubun": "ISSUE", "include": "Y", "reason": "기본값 변경 — 동작 규격이 바뀜",
     "redmine": "25999", "sensitive": False,
     "public": "Changed the default roaming threshold.",
     "apply_note": "[WLAN] Changed the default roaming threshold for faster handover."},
    {"no": 3, "issue": "[VoC] 특정 고객 전용 빌드에서 EmKit 스캐너 프로파일 저장 실패 (내부 티켓 SCN-4412)",
     "team": "SW1", "customer": "Nordwind", "gubun": "VOC", "include": "Y",
     "reason": "고객 요청 · 일반 OS 반영 · 민감 표현 순화함", "redmine": "31776",
     "sensitive": True,
     "public": "Fixed scanner profile save failure on a customer build.",
     "apply_note": "[Scanner] Fixed an issue where scanner profiles were not saved."},
    {"no": 4, "issue": "저전력 시 밝기·음량 제한 동작 개선", "team": "SW3", "customer": "",
     "gubun": "ISSUE", "include": "Y", "reason": "동작 로직 개선", "redmine": "31054",
     "sensitive": False,
     "public": "Improved battery safety behavior at low battery.",
     "apply_note": "[Battery] Improved battery safety by limiting volume and brightness at low battery."},
    {"no": 5, "issue": "EmSettings 내부 로그 옵션 정리", "team": "DQA", "customer": "",
     "gubun": "ISSUE", "include": "X", "reason": "내부(도구)", "redmine": "32907",
     "sensitive": False, "public": "Reorganized internal log options.",
     "apply_note": "[Settings] Reorganized internal log options."},
    {"no": 6, "issue": "특정 조건에서 앱이 종료되는 문제", "team": "SW2", "customer": "",
     "gubun": "ISSUE", "include": "X", "reason": "결함수정", "redmine": "31402",
     "sensitive": False, "public": "Fixed an app termination issue.",
     "apply_note": "[System] Fixed an app termination issue."},
    {"no": 7, "issue": "사내 검증 중 발견한 표시 오류", "team": "DQA", "customer": "",
     "gubun": "ISSUE", "include": "X", "reason": "DQA", "redmine": "32723",
     "sensitive": False, "public": "Fixed a display error.",
     "apply_note": "[Settings] Fixed a display error."},
    {"no": 8, "issue": "타 모델 전용 카메라 튜닝", "team": "SW3", "customer": "",
     "gubun": "ISSUE", "include": "X", "reason": "타제품", "redmine": "30479",
     "sensitive": False, "public": "Updated camera tuning.",
     "apply_note": "[Camera] Updated camera tuning."},
]

_COMPONENTS = [
    ("known_issues", "2. Known Issues", ""),
    ("gms_package", "3. GMS Package", "15_202605"),
    ("sdk", "4. SDK", "3.41.09"),
    ("audio", "5. Audio parameter version information", "95.A.10"),
    ("touch", "6. Touch panel version information", "95.T.1C"),
    ("wifi_driver", "7. Wi-Fi version information — Driver", "2.0.9.24C"),
    ("wifi_firmware", "7. Wi-Fi version information — Firmware", "1.1.5.32767.0"),
    ("wifi_config", "7. Wi-Fi version information — Configuration", "95.W.04"),
    ("security_patch", "12. Android security patch level", "July 5, 2026"),
]

_MAILS = [
    {"subject": "RE: FOTA 등록 요청 - Carema DE", "when": "2026-08-18 13:33", "count": 2,
     "sender": "TA <pm_ta@pointmobile.com>",
     "att": "[첨부메일] FOTA 등록 요청.eml, [첨부메일] RE 회신.eml, [첨부메일] SN 목록.eml",
     "model": "PM95 외 3종", "from_os": "-", "to_os": "-",
     "verdict": "등록일 변경", "conf": 0.95, "state": "new",
     "reason": "본문에 '등록해주신 SN 에 대하여 등록일을 8/18 로 변경 요청' 이라고 적혀 있고, "
               "첨부 메일 3건에 대상 SN 이 나열되어 있습니다.",
     "sns": [("PM95-2411-8841", None), ("PM95-2411-8842", None),
             ("PM86-2405-1173", None), ("PM86-2405-1174", None),
             ("PM86-2405-1175", None), ("PM86-2405-1176", None),
             ("PM86-2405-1177", None), ("PM86-2405-1178", None),
             ("PM86-2405-1179", None), ("PM45-2312-0447", None),
             ("PM90-2402-6620", None)]},
    {"subject": "FOTA 등록 요청 - PM86 신규 12대", "when": "2026-08-18 10:41", "count": 1,
     "sender": "TA <pm_ta@pointmobile.com>", "att": "",
     "model": "PM86", "from_os": "86.06", "to_os": "86.07",
     "from_android": "11", "to_android": "13",
     "verdict": "신규 등록", "conf": 0.93, "state": "open",
     "reason": "신규 단말 12대의 White List 등록 요청입니다. 모델과 대상 OS 가 본문에 명시되어 있습니다.",
     "sns": [("PM86-2408-3301", None), ("PM86-2408-3302", None),
             ("PM86-2408-3303", {"request_id": "REQ-2026-0311",
                                 "created_at": "2026-08-11", "status": "승인"}),
             ("PM86-2408-3304", None), ("PM86-2408-3305", None)]},
    {"subject": "White List 등록 요청 (PM95)", "when": "2026-08-18 11:55", "count": 3,
     "sender": "TA <pm_ta@pointmobile.com>", "att": "",
     "model": "PM95", "from_os": "95.51", "to_os": "95.52",
     "from_android": "13", "to_android": "15",
     "verdict": "신규 등록", "conf": 0.88, "state": "open",
     "reason": "회신 사슬 안에 SN 4개가 정리되어 있습니다.",
     "sns": [("PM95-2412-1001", None), ("PM95-2412-1002", None),
             ("PM95-2412-1003", None), ("PM95-2412-1004", None)]},
]

# 등록 확인 표 — 실제 verify_table 의 열 구성(sn · supportEndDate · osType · api)
_LICENSES = [
    {"doc_id": "PMDM-2026-0142", "date": "2026-08-18", "status": "대기중",
     "part_number": "PMDM-STD-100", "type": "Standard", "quantity": "100",
     "company": "Nordwind Logistik GmbH", "contact": "Klaus Berger",
     "email": "k.berger@example.de", "user_id": "nordwind-admin",
     "recovery": "it@example.de", "recipient": "k.berger@example.de",
     "by": "TA", "key": "", "note": "연간 계약 갱신분", "po": "PO_2026_0817.pdf",
     "confirm": ""},
]

_ISSUE_COLOR = {
    "Open": "red-6", "ReOpen": "deep-orange-7", "InProgress": "blue-7",
    "Monitoring": "purple-5", "Holding": "grey-7", "Resolved": "green-7",
    "Closed": "blue-grey-5", "Rejected": "brown-5",
}

#: 시연에 쓰는 이슈 — 실제 Redmine 이슈(salesissue)에서 가져왔다. 번호·제목·상태·
#: 소요 지표·타임라인이 모두 실제 값이다. 다만 이 시연 화면은 로그인 없이 열리므로
#: **사람 이름은 TA 로, 고객사·기관명은 [고객사]/공공기관으로 가렸다.**
#: 지표는 실제 계산(events.Timings)을 그대로 옮긴 값으로, 주말·한국 공휴일을 뺀
#: 업무일 기준이다.
_ISSUES = [
    {"id": 34220, "status": "Open", "assignee": "TA", "author": "TA",
     "ago": "오늘",
     "events": [
         {
             "kind": "created", "at": "08-21 08:34", "who": "TA",
             "status": "", "summary": "",
             "note": ('<p>【 Preset Conditions 】별도 설정 없음</p><p>【 Steps to reproduce 】Scan demo App 실행 &gt; I2of5 바코드 리딩</p><p>【 Expected 】정상 리딩 / 【 Actual 】리딩되지 않거나 값이 잘림</p>'),
         },
         {
             "kind": "field", "at": "08-21 08:35", "who": "TA",
             "status": "", "summary": "담당자: (없음) → TA",
             "note": (''),
         },
     ],
     "subject": "[ISSUE][PM3P][ED3650][고객사]I2of5 바코드 리딩이 잘 되지 않음.",
     "first": "-", "resolve": "-", "reopen": 0, "joined": 1, "comments": 0,
     "sb": "",
     "timeline": [("08-21 08:34", "TA", "이슈 생성 · 상태 Open"),
                  ("08-21 08:35", "TA", "담당자 지정")]},
    {"id": 33526, "status": "InProgress", "assignee": "TA", "author": "TA",
     "ago": "오늘",
     "events": [
         {
             "kind": "created", "at": "06-05 13:06", "who": "TA",
             "status": "", "summary": "",
             "note": ('<p>단말 사용 중 임의로 재부팅되는 현상이 보고되었습니다. 재현 빈도와 조건 확인이 필요합니다.</p>'),
         },
         {
             "kind": "status", "at": "06-05 13:09", "who": "TA",
             "status": "InProgress", "summary": "상태: Open → InProgress; 진행률: 0 → 10",
             "note": (''),
         },
         {
             "kind": "comment", "at": "06-11 10:20", "who": "TA",
             "status": "", "summary": "",
             "note": ('<p>커널 로그를 받아 재부팅 직전 구간을 확인했습니다. watchdog 관련 로그가 반복됩니다.</p>'),
         },
         {
             "kind": "comment", "at": "08-21 09:12", "who": "TA",
             "status": "", "summary": "",
             "note": ('<p>수정 빌드로 현장 검증 진행 중입니다.</p>'),
         },
     ],
     "subject": "[ISSUE][PM95_A13][고객사] 단말기 사용 도중에 재부팅 되는 현상",
     "first": "0.0 업무일", "resolve": "-", "reopen": 0, "joined": 4,
     "comments": 14, "sb": "",
     "timeline": [("06-05 13:06", "TA", "이슈 생성 · 상태 Open"),
                  ("06-05 13:09", "TA", "상태 InProgress · 로그 요청"),
                  ("06-11 10:20", "TA", "커널 로그 분석 — 재부팅 직전 구간 확인"),
                  ("08-21 09:12", "TA", "수정 빌드 검증 진행 중")]},
    {"id": 33450, "status": "Monitoring", "assignee": "TA", "author": "TA",
     "ago": "2일 전",
     "events": [
         {
             "kind": "created", "at": "05-19 13:25", "who": "TA",
             "status": "", "summary": "",
             "note": ('<p>【 Preset Conditions 】별도 설정 없음</p><p>【 Steps to reproduce 】Scan demo App 실행 &gt; sample barcode 리딩</p>'),
         },
         {
             "kind": "status", "at": "05-19 17:11", "who": "TA",
             "status": "InProgress", "summary": "상태: Open → InProgress; 진행률: 0 → 10",
             "note": ('<p>개선 가능한 스캐너 파라미터를 일부 찾았습니다. 추가 개선이 가능한지 모듈 제조사에 문의했습니다.</p>'),
         },
         {
             "kind": "attachment", "at": "05-20 16:19", "who": "TA",
             "status": "", "summary": "첨부 추가: scanner_preset.apk",
             "note": ('<p>스캐너 설정을 초기화하고 파라미터를 적용하는 앱을 전달했습니다.</p>'),
         },
         {
             "kind": "status", "at": "06-04 14:17", "who": "TA",
             "status": "Monitoring", "summary": "상태: InProgress → Monitoring; 진행률: 10 → 50",
             "note": ('<p>고객사가 전달한 방안을 적용하지 않기로 하여 경과를 관찰합니다.</p>'),
         },
         {
             "kind": "field", "at": "08-19 11:52", "who": "TA",
             "status": "", "summary": "완료 기한: (없음) → 2026-09-11",
             "note": ('<p>모듈 제조사로부터 개선된 디코드 솔루션을 받았습니다. 증상 개선은 확인했으나 재검증이 필요합니다.</p>'),
         },
     ],
     "subject": "[ISSUE][PM90][고객사]특정 Code128 barcode가 인식이 되지 않음",
     "first": "0.2 업무일", "resolve": "-", "reopen": 0, "joined": 3,
     "comments": 9, "sb": "",
     "timeline": [("05-19 13:25", "TA", "이슈 생성 · 상태 Open"),
                  ("05-19 17:11", "TA", "상태 InProgress · 개선 가능한 스캐너 "
                                        "파라미터 확인"),
                  ("05-20 16:19", "TA", "설정 초기화 후 개선 앱 전달"),
                  ("06-04 14:17", "TA", "상태 Monitoring · 현장 적용 보류, 경과 관찰"),
                  ("08-19 11:52", "TA", "디코드 솔루션 개선본 입수 — 재검증 예정")]},
    {"id": 33282, "status": "ReOpen", "assignee": "TA", "author": "TA",
     "ago": "오늘",
     "events": [
         {
             "kind": "created", "at": "04-17 14:20", "who": "TA",
             "status": "", "summary": "",
             "note": ('<p>공공기관 납품 건의 추가 요구 사항입니다.</p>'),
         },
         {
             "kind": "comment", "at": "04-20 11:22", "who": "TA",
             "status": "", "summary": "",
             "note": ('<p>요구 사항을 항목별로 정리했습니다.</p>'),
         },
         {
             "kind": "status", "at": "04-27 16:35", "who": "TA",
             "status": "Resolved", "summary": "상태: InProgress → Resolved; 진행률: 10 → 90",
             "note": (''),
         },
         {
             "kind": "status", "at": "05-14 10:05", "who": "TA",
             "status": "ReOpen", "summary": "상태: Resolved → ReOpen",
             "note": ('<p>추가 요구 사항이 접수되어 다시 엽니다.</p>'),
         },
         {
             "kind": "comment", "at": "08-21 09:40", "who": "TA",
             "status": "", "summary": "",
             "note": ('<p>잔여 항목을 진행 중입니다.</p>'),
         },
     ],
     "subject": "[VOC][PM95][Android13] 공공기관 요구 사항",
     "first": "0.9 업무일", "resolve": "6.1 업무일", "reopen": 3, "joined": 11,
     "comments": 33, "sb": "",
     "timeline": [("04-17 14:20", "TA", "이슈 생성 · 상태 Open"),
                  ("04-20 11:22", "TA", "요구 사항 정리 · 상태 InProgress"),
                  ("04-27 16:35", "TA", "1차 반영 · 상태 Resolved"),
                  ("05-14 10:05", "TA", "추가 요구 접수 · 상태 ReOpen"),
                  ("08-21 09:40", "TA", "잔여 항목 진행 중 (재오픈 3회)")]},
    {"id": 33947, "status": "Resolved", "assignee": "TA", "author": "TA",
     "ago": "30일 전",
     "events": [
         {
             "kind": "created", "at": "07-22 13:26", "who": "TA",
             "status": "", "summary": "",
             "note": ('<p>【 Preset Conditions 】</p><ul><li>대상 기기: PM451 / 대상 펌웨어: 41.57</li><li>PMDM 에서 생성한 Wi-Fi(WLAN) 프로파일</li></ul><p>Enrollment 중 프로파일 적용 단계에서 Runtime Exception 이 발생합니다. 41.55 에서는 정상입니다.</p>'),
         },
         {
             "kind": "status", "at": "07-22 18:32", "who": "TA",
             "status": "Resolved", "summary": "상태: Open → Resolved; 진행률: 0 → 90",
             "note": ('<p>PMDMAgent 를 수정했습니다.</p><ul><li>Android 15 미만에서 지원하지 않는 기능을 호출하여 발생한 문제</li><li>PMDMAgent v1.12.6 이상에서 해결됩니다.</li></ul>'),
         },
     ],
     "subject": "[Issue][PMDMAgent]PM451 41.57에서 PMDM Enrollment 시 WLAN "
                "프로파일 적용 중 Runtime Exception 발생 (41.55에서는 정상)",
     "first": "0.2 업무일", "resolve": "0.2 업무일", "reopen": 0, "joined": 1,
     "comments": 1, "sb": "draft",
     "timeline": [("07-22 13:26", "TA", "이슈 생성 · 상태 Open"),
                  ("07-22 18:32", "TA", "PMDMAgent 수정 — Android 15 미만 "
                                        "미지원 기능 호출 · 상태 Resolved")]},
    {"id": 33499, "status": "Resolved", "assignee": "TA", "author": "TA",
     "ago": "78일 전",
     "events": [
         {
             "kind": "created", "at": "05-29 09:02", "who": "TA",
             "status": "", "summary": "",
             "note": ('<p><em>Applicable:</em> PM90 / Ethernet Settings / OEMConfig</p><p>Ethernet Proxy 를 PAC URL 방식으로 설정해도 단말에 반영되지 않습니다.</p>'),
         },
         {
             "kind": "field", "at": "05-29 09:02", "who": "TA",
             "status": "", "summary": "담당자: TA → TA",
             "note": (''),
         },
         {
             "kind": "status", "at": "06-04 14:56", "who": "TA",
             "status": "Resolved", "summary": "상태: Open → Resolved; 진행률: 0 → 90",
             "note": ('<p>OEMConfig(EmkitAgent)를 수정했습니다.</p><ul><li>IP Assignment 가 Keep current 이면 Proxy 를 설정하지 못하던 문제</li><li>v2.14.3 이상에서 해결됩니다.</li></ul>'),
         },
     ],
     "subject": "[Issue][OEMConfig] Ethernet Proxy 설정 적용 불가 이슈",
     "first": "3.2 업무일", "resolve": "3.2 업무일", "reopen": 0, "joined": 2,
     "comments": 1, "sb": "notified",
     "timeline": [("05-29 09:02", "TA", "이슈 생성 · 상태 Open"),
                  ("05-29 09:02", "TA", "담당자 지정"),
                  ("06-04 14:56", "TA", "EmkitAgent 수정 — IP Assignment 가 "
                                        "Keep current 일 때 Proxy 미적용 · 상태 Resolved")]},
    {"id": 33498, "status": "Resolved", "assignee": "TA", "author": "TA",
     "ago": "80일 전",
     "events": [
         {
             "kind": "created", "at": "05-29 08:57", "who": "TA",
             "status": "", "summary": "",
             "note": ('<p>PMDM 프로파일에서 Custom Intent 필드를 비우거나 기본값으로 되돌리는 기능을 요청합니다.</p>'),
         },
         {
             "kind": "comment", "at": "06-01 15:52", "who": "TA",
             "status": "", "summary": "",
             "note": ('<p>요구 사항을 확인했습니다. Console 과 Agent 양쪽 변경이 필요합니다.</p>'),
         },
         {
             "kind": "status", "at": "06-02 09:45", "who": "TA",
             "status": "Resolved", "summary": "상태: Open → Resolved; 진행률: 0 → 90",
             "note": ('<p>각 입력칸에 Default / Empty 선택을 추가했습니다.</p>'),
         },
     ],
     "subject": "[VoC][PMDM-ENT]PMDM 프로파일을 통해 단말기의 Custom Intent 필드 "
                "값을 명시적으로 비우는 기능 및 기본 설정으로 원복하는 기능 요청",
     "first": "1.3 업무일", "resolve": "2.0 업무일", "reopen": 0, "joined": 3,
     "comments": 4, "sb": "queued",
     "timeline": [("05-29 08:57", "TA", "이슈 생성 · 상태 Open"),
                  ("06-01 15:52", "TA", "요구 사항 확인 · 개발 검토"),
                  ("06-02 09:45", "TA", "Default/Empty 선택 기능 반영 · "
                                        "상태 Resolved")]},
    {"id": 33514, "status": "Resolved", "assignee": "TA", "author": "TA",
     "ago": "67일 전",
     "events": [
         {
             "kind": "created", "at": "06-02 13:15", "who": "TA",
             "status": "", "summary": "",
             "note": ('<p>PM5 를 HID 모드로 쓸 때 스캔 후 호스트의 소프트 키보드가 올라오게 해 달라는 요청입니다.</p>'),
         },
         {
             "kind": "comment", "at": "06-02 14:04", "who": "TA",
             "status": "", "summary": "",
             "note": ('<p>요구 사항을 확인했습니다.</p>'),
         },
         {
             "kind": "attachment", "at": "06-09 10:30", "who": "TA",
             "status": "", "summary": "첨부 추가: PM5_9214B1_test.bin",
             "note": ('<p>MCU·BT FW 시험 빌드를 전달했습니다.</p>'),
         },
         {
             "kind": "status", "at": "06-15 16:00", "who": "TA",
             "status": "Resolved", "summary": "상태: InProgress → Resolved; 진행률: 10 → 90",
             "note": ('<p>92.14 B1 에 반영했습니다. 설정 바코드로도 켤 수 있습니다.</p>'),
         },
     ],
     "subject": "[VoC][PM5] Keyboard auto popup feature",
     "first": "0.0 업무일", "resolve": "8.1 업무일", "reopen": 0, "joined": 3,
     "comments": 9, "sb": "uploaded",
     "timeline": [("06-02 13:15", "TA", "이슈 생성 · 상태 Open"),
                  ("06-02 14:04", "TA", "요구 사항 확인"),
                  ("06-09 10:30", "TA", "MCU·BT FW 시험 빌드 전달"),
                  ("06-15 16:00", "TA", "92.14 B1 반영 · 상태 Resolved")]},
]

#: Redmine 본문(Textile)을 렌더한 결과에 입히는 최소 서식 — 실제 화면과 같다.
_RM_NOTE_CSS = """
.rm-note { word-break: break-word; }
.rm-note p { margin: 0 0 .5em; }
.rm-note ul, .rm-note ol { margin: .25em 0 .5em; padding-left: 1.4em; }
.rm-note ul { list-style: disc; }
.rm-note ol { list-style: decimal; }
.rm-note li { margin: .1em 0; }
.rm-note table {
  border-collapse: collapse; margin: .4em 0; font-size: .95em;
  display: block; overflow-x: auto; max-width: 100%;
}
.rm-note th, .rm-note td { border: 1px solid #d0d0d0; padding: 3px 8px; }
.rm-note th { background: #f2f4f3; font-weight: 600; text-align: left; }
.rm-note code { background: #f0f0f0; padding: 0 3px; border-radius: 3px; }
.rm-note a { color: #1976d2; }
"""

#: 이력 아이콘 — 실제 화면(issues._EVENT_ICON)과 같다.
_EVENT_ICON = {
    "created": ("add_circle", "primary"),
    "status": ("flag", "green-7"),
    "comment": ("chat_bubble", "blue-grey-6"),
    "attachment": ("attach_file", "deep-purple-5"),
    "field": ("edit", "grey-6"),
}

#: 상태 칩 — 실제 DB 의 상태별 건수다.
_ISSUE_STATUS_COUNTS = [
    ("Closed", 740), ("Resolved", 670), ("Open", 79), ("InProgress", 68),
    ("Monitoring", 29), ("ReOpen", 13), ("Holding", 7), ("Rejected", 6),
]

#: 통계 — 실제 집계값(app.issuetrack.stats).
_STAT_MONTHS = ["2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02",
                "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
_STAT_CREATED = [33, 26, 36, 19, 26, 20, 18, 20, 36, 15, 19, 8]
_STAT_RESOLVED = [25, 25, 11, 24, 23, 21, 18, 15, 21, 13, 12, 8]
_STAT_STATUS = [
    ("Closed", 740, "#6b7f8c"), ("Resolved", 670, "#4A7C67"),
    ("Open", 79, "#e05252"), ("InProgress", 68, "#3b6ea5"),
    ("Monitoring", 29, "#8e5fbf"), ("ReOpen", 13, "#e07a2c"),
    ("Holding", 7, "#8a8a8a"), ("Rejected", 6, "#8a6b5a"),
]
_STAT_ACTIONS = [
    ("코멘트", 728, "#c0705a"), ("분석 착수", 240, "#3b6ea5"),
    ("바로 해결", 203, "#4A7C67"), ("담당 배정", 183, "#d0a24c"),
    ("기타 변경", 121, "#8a8a8a"), ("첨부", 4, "#7a9a8b"),
    ("상태 변경", 2, "#8e5fbf"),
]
#: 담당자 이름은 가린다 — 이 화면은 로그인 없이 열린다. 건수는 실제 분포다.
_STAT_ASSIGNEES = [
    {"name": "담당자 A", "total": 271, "open": 24, "lead": 18.4},
    {"name": "담당자 B", "total": 214, "open": 31, "lead": 27.1},
    {"name": "담당자 C", "total": 188, "open": 17, "lead": 15.9},
    {"name": "담당자 D", "total": 141, "open": 22, "lead": 31.6},
    {"name": "담당자 E", "total": 118, "open": 12, "lead": 12.3},
    {"name": "담당자 F", "total": 96, "open": 9, "lead": 20.8},
]

#: 실제 화면(sb_docs)의 상태 라벨·색을 그대로 쓴다.
_SB_STATE = {
    "": ("대상 아님", "blue-grey-3"),
    "backlog": ("소급 대기", "grey-7"),
    "queued": ("생성 대기", "amber-8"),
    "draft": ("초안", "blue-7"),
    "notified": ("검토 요청됨", "indigo-6"),
    "approved": ("승인됨", "teal-7"),
    "uploaded": ("업로드 완료", "green-7"),
    "discarded": ("폐기", "blue-grey-4"),
}

#: 공개 등급 — 실제 화면과 같은 네 가지.
_SB_GRADE = {
    "public": ("전체 공개", "green-7"),
    "partner": ("파트너 공개", "blue-7"),
    "ascp": ("ASC·ASP 공개", "purple-6"),
    "internal": ("내부 공유", "red-7"),
}

#: 시연에 쓰는 Solution Buffer 문서 — 실제 생성된 문서를 그대로 가져왔다.
#: 넷 다 공개 등급이 public 이라 고객에게 나가도 되는 내용이고, 고객사 정보가
#: 들어 있지 않다. 본문은 실제와 같은 HTML 이다.
_SB_DOCS = {
    33947: {
        "stamps": "해결 2026-07-22 18:32 · 생성 2026-07-22 18:40 · 토큰 8,412/1,930",
        "url": "",
        "title": "[TR] PM451 41.57에서 PMDM Enrollment 중 WLAN 프로파일 적용 시 "
                 "Runtime Exception",
        "grade": "public", "trouble": True, "revision": 1,
        "applies_to": "PM451 / FW 41.57 — PMDMAgent v1.12.6 이상에서 수정",
        "labels": ["sb"],
        "reason": "배포된 PMDMAgent 버전으로 해결되는 문제이고 고객사 정보 없이 "
                  "설명할 수 있어 제약 없이 공개한다.",
        "body": ("<p><strong>증상</strong></p><ul>"
                 "<li>PM451(FW 41.57)에서 PMDM Enrollment 를 진행하면, PMDM 에 미리 "
                 "만들어 둔 WLAN 프로파일이 적용되는 단계에서 Runtime Exception 이 "
                 "발생한다.</li>"
                 "<li>같은 조건에서 FW 41.55 로 테스트하면 발생하지 않는다.</li></ul>"
                 "<p><strong>원인</strong></p><ul>"
                 "<li>OS 변경으로 보였지만 <strong>OS 문제가 아니라 PMDMAgent "
                 "문제</strong>였다.</li>"
                 "<li>PMDMAgent 가 <strong>Android 15 미만에서 지원하지 않는 기능을 "
                 "호출</strong>하고 있었다.</li></ul>"
                 "<p><strong>해결</strong></p><ul>"
                 "<li>PMDMAgent 를 <strong>v1.12.6 이상</strong>으로 업데이트한다. "
                 "OS 를 다시 올릴 필요는 없다.</li>"
                 "<li>PMDM-ENT 서버에 해당 버전이 올라가 있다.</li></ul>"),
    },
    33499: {
        "stamps": "해결 2026-06-04 14:56 · 생성 2026-06-04 15:02 · 검토요청 2026-06-05 09:10 · 토큰 9,077/2,145",
        "url": "",
        "title": "[TR] OEMConfig에서 Ethernet Proxy(PAC URL) 설정이 단말에 "
                 "적용되지 않을 때",
        "grade": "public", "trouble": True, "revision": 1,
        "applies_to": "PM90 등 Ethernet 을 지원하는 모델 / OEMConfig(EmkitAgent) "
                      "v2.14.2 이하 — v2.14.3에서 수정",
        "labels": ["sb"],
        "reason": "배포된 EmkitAgent 버전으로 해결되는 문제이고, 재현 조건만으로 "
                  "설명할 수 있어 제약 없이 공개한다.",
        "body": ("<p><strong>증상</strong></p><ul>"
                 "<li>OEMConfig 의 Ethernet Settings 에서 Proxy 를 Proxy "
                 "Auto-Config(PAC URL) 방식으로 설정해도 단말에 반영되지 않는다.</li>"
                 "<li>단말의 Ethernet Settings 화면에서 Proxy settings 항목이 계속 "
                 "<em>None</em> 으로 표시된다.</li>"
                 "<li>같은 단말에서 PMDM 으로 동일한 Ethernet Proxy 설정을 내리면 "
                 "정상 반영된다.</li>"
                 "<li>Ethernet 연결 자체(IP·Gateway·DNS 할당, On/Off)는 정상이다.</li>"
                 "</ul><p><strong>원인</strong></p><ul>"
                 "<li>OEMConfig(EmkitAgent)에서 <strong>IP Assignment 항목이 "
                 "\'Keep current\' 로 되어 있으면 Proxy 설정이 함께 적용되지 않는 "
                 "결함</strong>이 있었다.</li></ul>"
                 "<p><strong>해결</strong></p><ul>"
                 "<li>EmkitAgent 를 <strong>v2.14.3 이상</strong>으로 업데이트한다. "
                 "IP Assignment 가 Keep current 여도 Proxy 설정이 적용된다.</li></ul>"),
    },
    33498: {
        "stamps": "해결 2026-06-02 09:45 · 생성 —",
        "url": "",
        "title": "PMDM 프로파일에서 Custom Intent 값을 비우거나 기본값으로 되돌리기",
        "grade": "public", "trouble": False, "revision": 1,
        "applies_to": "PMDM Console v5.9.1.0 이상 / PMDMAgent v1.12.2.1 이상 — "
                      "ScanSettings 의 Custom Intent 프로파일",
        "labels": ["sb"],
        "reason": "제품 기능 안내이고 고객사 정보가 없어 제약 없이 공개한다.",
        "body": ("<p><strong>이전 제약</strong></p><ul>"
                 "<li>PMDM Web UI 에서 Custom Intent 필드에 값을 넣으면 단말에 "
                 "반영되지만, <strong>필드를 비워 두고 적용하면 그 필드는 아예 "
                 "전송되지 않았다.</strong></li>"
                 "<li>그래서 단말에 한 번 들어간 값을 PMDM 으로 <em>비우거나</em> "
                 "<em>기본값으로 되돌릴</em> 방법이 없었다. 단말마다 손으로 지워야 "
                 "했다.</li></ul>"
                 "<p><strong>변경된 동작</strong></p><ul>"
                 "<li>Custom Intent 의 각 입력칸을 선택하면 <strong>Default / "
                 "Empty</strong> 메뉴가 나타난다.</li>"
                 "<li>Default 는 항목의 기본값을 채우고, Empty 는 프로파일 적용 시 "
                 "단말의 값을 빈칸으로 만든다.</li></ul>"),
    },
    33514: {
        "stamps": "해결 2026-06-15 16:00 · 생성 2026-06-15 16:07 · 검토요청 2026-06-16 09:22 · 업로드 2026-06-17 10:05 · 토큰 11,204/2,806",
        "url": "https://wiki.pointmobile.co.kr/display/SB",
        "title": "PM5 HID 모드에서 바코드 스캔 후 소프트 키보드를 자동으로 띄우는 방법",
        "grade": "public", "trouble": False, "revision": 1,
        "applies_to": "PM5 / HID 모드 (MCU·BT FW 92.14 B1 이상) — iOS, Android 호스트",
        "labels": ["sb", "posted"],
        "reason": "제품 기능 안내이고 고객사 정보가 없어 제약 없이 공개한다.",
        "body": ("<p><strong>배경</strong></p><ul>"
                 "<li>PM5 를 iOS·Android 호스트에 HID 모드로 연결해 쓰면, 바코드를 "
                 "읽은 뒤 추가 데이터를 손으로 입력해야 하는 작업이 있다.</li>"
                 "<li>그런데 HID 키보드가 연결돼 있으면 호스트의 소프트웨어 키보드가 "
                 "내려가 있어, 스캔 후 바로 타이핑할 수 없다.</li></ul>"
                 "<p><strong>기능</strong></p><ul>"
                 "<li><strong>Keyboard auto popup</strong> 설정을 켜면, 바코드 "
                 "데이터를 보낸 뒤 Soft Keyboard Toggle Key(Eject, 0xB8)를 함께 "
                 "전송해 호스트의 소프트 키보드를 띄운다.</li>"
                 "<li>기본값은 꺼짐이며, 이때는 바코드 데이터만 전송한다. HID "
                 "모드에서만 동작한다.</li></ul>"
                 "<p><strong>설정 방법 — 설정 바코드</strong></p>"
                 "<table><tr><th>동작</th><th>바코드 데이터</th></tr>"
                 "<tr><td>사용</td><td>{BTHHIDPOP01}</td></tr>"
                 "<tr><td>사용 안 함(기본)</td><td>{BTHHIDPOP00}</td></tr></table>"
                 "<p>RingControl 앱을 쓰지 않는 환경도 설정 바코드만으로 적용할 수 "
                 "있다.</p>"),
    },
}


# ══════════════════════════════════════════════════════════════════════════
# 공통
# ══════════════════════════════════════════════════════════════════════════
def _fresh() -> dict:
    # 실제 시스템처럼 **두 갈래**로 나눈다 — Submission(제작) 3단계 ·
    # Approval(배포) 7단계. 같은 단계라도 갈래에 따라 이름과 하는 일이 다르다.
    # 시연은 모두 끝난 회차에서 시작한다(사용자 확정 2026-08-21).
    stages: dict = {}
    for _side, _keys in _SIDE_KEYS.items():
        _lst = []
        for _k in _keys:
            _s = deepcopy(_STAGE_DEF[_k])
            _s["side"] = _side
            if _side == "approval" and _k in _APPROVAL_LABEL:
                _s["label"] = _APPROVAL_LABEL[_k]
                # 이 두 단계는 Submission 승인본을 올리고 보내기만 한다 —
                # 다시 만들지 않으므로 AI 를 쓰지 않는다.
                _s["ai"] = False
                _s["result"] = (_RESULT_KB_UPLOAD if _k == "release_note_edit"
                                else _RESULT_NEWS_SEND)
                _s["steps"] = _APPROVAL_STEPS[_k]
            _s["status"] = (_APPROVAL_OPEN.get(_k, "done")
                            if _side == "approval" else "done")
            # 아직 실행하지 않은 단계에는 실행자를 적지 않는다.
            _s["by"] = "" if _s["status"] == "pending" else "TA"
            _lst.append(_s)
        stages[_side] = _lst
    return {
        "stages": stages,
        "rows": deepcopy(_SELECT_ROWS),
        "comps": {k: v for k, _lab, v in _COMPONENTS},
        "mid": "approved", "final": "approved", "regen": 0,
        "mails": deepcopy(_MAILS),
        "licenses": deepcopy(_LICENSES),
        "issues": deepcopy(_ISSUES),
        "nl_cards": deepcopy(_NL_CARDS),
    }


def _badge(text: str, color: str):
    return ui.badge(text, color=color)


def _explain(overview: str, ai: "list | None" = None) -> None:
    """화면마다 붙는 설명 — 개요와 AI 활용.

    ai 는 그 화면에서 AI 가 실제로 하는 일을 순서대로 적은 목록이다.
    '읽는 것 → 만드는 것' 이 드러나게 쓴다(예: Redmine 이슈를 읽고 요약 → RN 생성).
    AI 를 쓰지 않는 화면은 **비워 둔다** — 'AI 를 쓰지 않습니다' 라고 적지 않고
    칸 자체를 그리지 않는다(사용자 확정 2026-08-21).
    """
    with ui.expansion("이 화면은 무엇을 하나요?", value=True).classes("w-full q-mb-sm") \
            .props("dense header-class=text-weight-medium"):
        with ui.row().classes("items-start gap-3 no-wrap q-mb-sm"):
            ui.label("개요").classes("text-caption text-weight-bold").style(
                "min-width:72px; color:#4A5751")
            ui.label(overview).classes("text-caption").style(
                "color:#4A5751; white-space:normal; line-height:1.6")
        if not ai:
            return
        with ui.row().classes("items-start gap-3 no-wrap"):
            ui.label("AI 활용").classes("text-caption text-weight-bold").style(
                "min-width:72px; color:#7A5318")
            with ui.column().classes("gap-0").style("min-width:0"):
                for item in ai:
                    with ui.row().classes("items-start gap-2 no-wrap"):
                        ui.label("·").classes("text-caption").style("color:#7A5318")
                        ui.label(item).classes("text-caption").style(
                            "color:#4A5751; white-space:normal; line-height:1.6")


def _mono(text: str, height: str = "") -> None:
    style = ("font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:12.5px; "
             "white-space:pre-wrap; line-height:1.65; background:#F5F7F5; "
             "border:1px solid #d5ded8; border-radius:6px; padding:12px 14px; ")
    if height:
        style += "max-height:" + height + "; overflow:auto;"
    ui.label(text).classes("w-full").style(style)


# ══════════════════════════════════════════════════════════════════════════
# OS Update
# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
_RN_PDF = "/static/demo/PM95_A15_Release_Note_95.52_for_Sales.pdf"
_RN_FULL_PDF = "/static/demo/PM95_A15_Release_Note_95.52_full.pdf"
_RN_DOCX = "/static/demo/PM95_A15_Release_Note_95.52_for_Sales.docx"

_NL_TOPICS = {"security": "보안", "network": "네트워크",
              "pmdm": "PMDM/ZeroTouch", "common": "일반"}

_NL_CARDS = [
    {"topic": "security", "title_ko": "보안 패치 2026년 7월 5일 적용",
     "body_ko": "Android 보안 패치 수준을 2026년 7월 5일자로 올렸습니다.",
     "title_en": "Security patch level updated to July 5, 2026",
     "body_en": "The Android security patch level has been updated to July 5, 2026."},
    {"topic": "common", "title_ko": "스캐너 하드웨어 키 모드 추가",
     "body_ko": "스캔 키를 하드웨어 키로 직접 지정해 쓸 수 있는 모드가 추가되었습니다.",
     "title_en": "HW Scan Key Mode added",
     "body_en": "A new mode lets you assign the scan trigger to a hardware key."},
    {"topic": "network", "title_ko": "Wi-Fi 로밍 기본값 변경",
     "body_ko": "AP 간 전환이 더 빨라지도록 로밍 기준값 기본 설정을 조정했습니다.",
     "title_en": "Wi-Fi roaming default changed",
     "body_en": "The default roaming threshold was changed for faster handover."},
    {"topic": "common", "title_ko": "저전력 시 동작 개선",
     "body_ko": "배터리가 부족할 때 밝기와 음량을 제한해 사용 시간을 늘립니다.",
     "title_en": "Improved low-battery behavior",
     "body_en": "Volume and brightness are limited at low battery to extend runtime."},
    {"topic": "common", "title_ko": "스캐너 프로파일 저장 문제 수정",
     "body_ko": "일부 환경에서 스캐너 프로파일이 저장되지 않던 문제를 고쳤습니다.",
     "title_en": "Fixed scanner profile save issue",
     "body_en": "Fixed an issue where scanner profiles were not saved."},
]

_SP_CHANGES = [
    {"why": "최신 OS 버전 표기를 이번 회차로 교체",
     "old": "Latest OS version : 95.51 (Mar 31 2026)",
     "new": "Latest OS version : 95.52 (Jul 20 2026)"},
    {"why": "보안 패치 수준 갱신",
     "old": "Android security patch level : June 5, 2026",
     "new": "Android security patch level : July 5, 2026"},
    {"why": "Release Note 링크를 95.52 판으로 교체",
     "old": "PM95_A15_Release_Note_95.51 for Sales.pdf",
     "new": "PM95_A15_Release_Note_95.52 for Sales.pdf"},
    {"why": "SDK 버전 표기 갱신", "old": "EmKit SDK : 3.40.11", "new": "EmKit SDK : 3.41.09"},
]
_SP_FAILED = ["구형 표기('OS Ver.')를 찾지 못해 건너뜀 — 문서에 해당 줄이 없습니다."]

_KB_ITEMS = [
    ("NEW", "Scanner", "Added the HW Scan Key Mode."),
    ("NEW", "WLAN", "Added an option for roaming threshold."),
    ("IMPROVED", "Battery", "Improved battery safety at low battery."),
    ("IMPROVED", "Scanner", "Fixed an issue where scanner profiles were not saved."),
    ("IMPROVED", "EmKit", "Improved log backup feature reliability."),
    ("FIXED", "System", "Improved overall system stability and fixed minor bugs."),
]


def _iframe(src: str, height: str = "62vh") -> None:
    """문서 미리보기 iframe.

    **sanitize=False 가 반드시 필요하다** — NiceGUI 의 ui.html 은 기본으로 내용을 정화하면서
    <iframe> 을 통째로 지운다. 그러면 미리보기 자리가 빈 칸으로만 남는다(실측).
    실제 화면(portal_edit._preview_iframe · mid_review.render_rn_preview)도 같은 이유로
    sanitize=False 를 준다.
    """
    ui.html(
        '<iframe src="' + src + '" style="width:100%; min-height:' + height
        + '; height:' + height + '; border:1px solid #d5ded8; border-radius:6px; '
        'background:#fff;"></iframe>',
        sanitize=False,
    ).classes("w-full")


def _srcdoc(doc: str, height: str = "58vh") -> None:
    """HTML 문자열을 그대로 그리는 iframe(srcdoc).

    메일 미리보기용. 메일 서식(MsoNormal·맑은 고딕)을 화면에 그대로 풀면 앱 CSS 와
    섞여 실제와 다르게 보이므로 iframe 안에 가둔다 — 실제 화면(mail_preview)도 같다.
    """
    import html as _h

    ui.html(
        '<iframe srcdoc="' + _h.escape(doc, quote=True) + '" sandbox="" '
        'style="width:100%; min-height:' + height + '; height:' + height + '; '
        'border:1px solid #dcdcdc; border-radius:6px; background:#fff;"></iframe>',
        sanitize=False,
    ).classes("w-full")


# ── 단계별 검수 내용 ──────────────────────────────────────────────────────
def _sec(title: str) -> None:
    """검수 팝업 안의 구역 제목 — 실제 화면은 한 화면을 내려가며 본다."""
    ui.separator().classes("q-my-md")
    ui.label(title).classes("text-subtitle2").style("font-weight:700")


def _review_rn(S: dict, st: dict) -> None:
    """② 검수 — 실제 화면(rn_review.render_rn_review)과 **같은 차례**로 편다.

    탭으로 나누지 않는다. 실제 화면도 한 화면을 내려가며 보는 구성이고, 위에서
    만들어진 문서를 먼저 확인한 뒤 아래에서 고치고 결재하는 흐름이다.
      문서 미리보기 → 공개 선별표 → 입력값 → 1차 RN 승인 → 2차 최종 승인
    """
    ui.label("만들어진 문서를 먼저 보고, 아래에서 공개 항목과 입력값을 고친 뒤 "
             "결재합니다.").classes("text-caption text-grey-7")
    _sec("문서 미리보기")
    _rn_preview_docs()
    _sec("공개 선별표")
    _rn_selection(S)
    _sec("입력값")
    _rn_inputs(S)
    _sec("결재")
    _rn_approval(S)


def _rn_selection(S: dict) -> None:
    ui.label("AI가 이슈 40건을 판정한 결과입니다. 기본값은 전부 반영이고 사람이 빼는 방향으로 "
             "검수합니다. 바꾼 뒤 'RN 재생성'을 누르면 오른쪽 Revision History 가 규칙대로 "
             "다시 계산됩니다.").classes("text-caption text-grey-7 q-mb-sm")

    @ui.refreshable
    def table() -> None:
        n_inc = sum(1 for r in S["rows"] if r["include"] == "Y")
        with ui.row().classes("items-center gap-2 q-mb-sm"):
            _badge("반영 " + str(n_inc), "green-8")
            _badge("미반영 " + str(len(S["rows"]) - n_inc), "grey-6")
            ui.label("이슈 40건 중 대표 8건").classes("text-caption text-grey-7")
        for r in S["rows"]:
            with ui.card().classes("w-full q-mb-xs").style(
                    "border-left:4px solid " + ("#3C6553" if r["include"] == "Y" else "#c8cdca")):
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    ui.link("#" + r["redmine"], _REDMINE + r["redmine"], new_tab=True) \
                        .classes("text-caption")
                    ui.label(r["issue"]).classes("text-body2").style(
                        "flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis")
                    if r["gubun"]:
                        ui.badge(r["gubun"], color="blue-grey-5").props("outline")
                    ui.badge(r["team"], color="blue-grey-6").props("outline") \
                        .tooltip("작성자 팀 — DQA 는 사내 검증 발견이라 제외 규칙 1번에 걸립니다")
                    if r["sensitive"]:
                        ui.badge("민감", color="red-7").props("outline") \
                            .tooltip("원문을 그대로 내보내면 안 되는 건 — 순화 문구를 따로 만들었습니다")
                    ui.toggle({"Y": "반영", "X": "미반영"}, value=r["include"],
                              on_change=lambda e, row=r: (row.update(include=e.value),
                                                          table.refresh())) \
                        .props("dense no-caps toggle-color=primary")
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label("판정 사유").classes("text-caption text-grey-7").style("min-width:60px")
                    ui.label(r["reason"]).classes("text-caption text-grey-8")
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label("수정 내용").classes("text-caption text-grey-7").style("min-width:60px")
                    ui.input(value=r.get("apply_note", ""),
                             on_change=lambda e, row=r: row.update(apply_note=e.value)) \
                        .props("dense outlined").classes("flex-grow")

    def _regen() -> None:
        # 다시 만든 결과는 위쪽 '문서 미리보기' 에 실린다 — 선별표 옆에 요약을
        # 따로 두지 않는다(사용자 확정 2026-08-21).
        S["regen"] += 1
        ui.notify("공개 항목을 반영해 문서를 다시 만들었습니다 — 위 문서 미리보기에서 "
                  "확인하세요", color="positive", multi_line=True)

    # 선별표는 한 폭으로 편다 — 항목마다 판정 사유와 수정 내용이 붙어 좁으면 읽기 어렵다.
    table()
    ui.button("저장 · RN 재생성", icon="refresh", on_click=_regen) \
        .props("color=primary").classes("q-mt-sm")


def _rn_inputs(S: dict) -> None:
    """문서 하단에 실리는 확인 항목 — 직전 버전에서 물려받아 사람이 고친다."""
    with ui.card().classes("w-full"):
        ui.label("직전 버전 문서에서 물려받은 값입니다. 고치면 '수동'으로 표시돼 "
                 "다시 실행해도 덮이지 않습니다.").classes("text-caption text-grey-7")
        ui.separator()
        for key, label, _v in _COMPONENTS:
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.label(label).classes("text-caption text-grey-7") \
                    .style("min-width:240px")
                ui.input(value=S["comps"].get(key, ""),
                         on_change=lambda e, k=key: S["comps"].update({k: e.value})) \
                    .props("dense outlined").classes("flex-grow")


def _rn_preview_docs() -> None:
    ui.label("실제 Word 템플릿으로 만든 문서입니다 — 이 회차 내용으로 렌더한 진짜 산출물입니다.") \
        .classes("text-caption text-grey-7")
    # 문서 두 종은 탭 대신 토글로 바꾼다 — 탭 안의 탭은 보기 어렵다.
    SALES, FULL = "RN for Sales (공개 8건)", "전체 이력 RN (V1~V2)"
    pick = ui.toggle([SALES, FULL], value=SALES).props("dense no-caps")

    @ui.refreshable
    def doc() -> None:
        if pick.value == FULL:
            with ui.row().classes("items-center gap-2 q-mb-xs"):
                ui.label("PM95_A15_Release_Note_95.52.pdf").classes(
                    "text-caption").style("font-family:ui-monospace,monospace")
                ui.label("이전 판(95.51)까지 이어붙인 이력본").classes(
                    "text-caption text-grey-7")
                ui.space()
                ui.link("PDF 새 창", _RN_FULL_PDF, new_tab=True).classes("text-caption")
            _iframe(_RN_FULL_PDF)
            return
        with ui.row().classes("items-center gap-2 q-mb-xs"):
            ui.label("PM95_A15_Release_Note_95.52 for Sales.pdf").classes(
                "text-caption").style("font-family:ui-monospace,monospace")
            ui.space()
            ui.link("docx 내려받기", _RN_DOCX, new_tab=True).classes("text-caption")
            ui.link("PDF 새 창", _RN_PDF, new_tab=True).classes("text-caption")
        _iframe(_RN_PDF)

    pick.on_value_change(lambda: doc.refresh())
    doc()


def _rn_approval(S: dict) -> None:
    LABEL = {"none": ("요청 전", "grey-6"), "requested": ("요청됨", "orange-8"),
             "approved": ("승인됨", "green-8"), "rejected": ("반려됨", "red-7")}

    @ui.refreshable
    def body() -> None:
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.label("1차 · RN 승인자 (History 관리용 RN)").classes("text-subtitle2")
                _badge(*LABEL[S["mid"]])
            ui.label("승인하면 전체 이력본이 PDF 로 확정되어 개발 RN 경로에 저장됩니다.") \
                .classes("text-caption text-grey-7")
            with ui.row().classes("gap-2 q-mt-sm"):
                if S["mid"] in ("none", "rejected"):
                    ui.button("RN 승인자에게 승인 요청",
                              on_click=lambda: _set("mid", "requested",
                                                    "승인 요청 메일을 보냈습니다 (시연 · 발송 없음)")) \
                        .props("color=primary size=sm")
                elif S["mid"] == "requested":
                    ui.button("승인", on_click=lambda: _set("mid", "approved",
                                                          "1차 승인 완료 · 이력 문서를 확정했습니다")) \
                        .props("color=primary size=sm")
                    ui.button("반려", on_click=lambda: _set("mid", "rejected", "반려했습니다")) \
                        .props("outline color=red-7 size=sm")
        if S["mid"] == "approved":
            with ui.card().classes("w-full q-mt-sm"):
                with ui.row().classes("items-center gap-2"):
                    ui.label("2차 · 최종 승인자 (RN for Sales)").classes("text-subtitle2")
                    _badge(*LABEL[S["final"]])
                ui.label("공개할 항목만 골라 만든 영업용 문서를 검수합니다.") \
                    .classes("text-caption text-grey-7")
                with ui.row().classes("gap-2 q-mt-sm"):
                    if S["final"] in ("none", "rejected"):
                        ui.button("최종 승인자에게 검수 요청",
                                  on_click=lambda: _set("final", "requested",
                                                        "검수 요청 메일을 보냈습니다 (시연 · 발송 없음)")) \
                            .props("color=primary size=sm")
                    elif S["final"] == "requested":
                        ui.button("검수 승인",
                                  on_click=lambda: _set("final", "approved",
                                                        "최종 승인 완료 · 단계를 완료 처리했습니다")) \
                            .props("color=primary size=sm")
                        ui.button("반려", on_click=lambda: _set("final", "rejected", "반려했습니다")) \
                            .props("outline color=red-7 size=sm")
            if S["final"] == "approved":
                with ui.card().classes("w-full q-mt-sm").style("border-left:4px solid #3C6553"):
                    ui.label("확정·저장된 산출물").classes("text-subtitle2")
                    _mono("RN for Sales PDF 확정: PM95_A15_Release_Note_95.52 for Sales.pdf\n"
                          "RN for Sales 저장 (2개) → \\\\192.168.123.160\\Sales\\ReleaseNotes\\PM95\n"
                          "전체 이력 RN 저장 (2개) → "
                          "\\\\192.168.123.104\\Project\\SQA\\Documents\\ReleaseNotes\\PM95\n"
                          "Release Note 제작 단계를 완료 처리했습니다.")

    def _set(key: str, value: str, msg: str) -> None:
        S[key] = value
        body.refresh()
        ui.notify(msg, color="positive" if value == "approved" else "primary", multi_line=True)

    body()


def _review_newsletter(S: dict) -> None:
    """⑦ 검수 — 카드 편집 + 실제 렌더된 뉴스레터 미리보기 + 발송 확인."""
    with ui.tabs().props("dense align=left no-caps").classes("w-full") as t:
        ui.tab("e", label="카드 편집")
        ui.tab("kr", label="미리보기")
    with ui.tab_panels(t, value="kr").classes("w-full"):
        with ui.tab_panel("e"):
            ui.label("카드를 고치면 발송 문안이 바뀝니다. 미리보기 탭은 실제 뉴스레터 템플릿으로 "
                     "렌더한 결과입니다.").classes("text-caption text-grey-7 q-mb-sm")
            for i, c in enumerate(S["nl_cards"]):
                with ui.card().classes("w-full q-mb-xs"):
                    with ui.row().classes("items-center gap-2 w-full no-wrap"):
                        ui.label("카드 " + str(i + 1)).classes("text-caption text-grey-6") \
                            .style("min-width:44px")
                        ui.select(_NL_TOPICS, value=c["topic"], label="주제",
                                  on_change=lambda e, cc=c: cc.update(topic=e.value)) \
                            .props("dense outlined").style("width:170px")
                    ui.input("제목 (KR)", value=c["title_ko"],
                             on_change=lambda e, cc=c: cc.update(title_ko=e.value)) \
                        .props("dense outlined").classes("w-full")
                    ui.textarea("본문 (KR)", value=c["body_ko"],
                                on_change=lambda e, cc=c: cc.update(body_ko=e.value)) \
                        .props("dense outlined autogrow").classes("w-full")
                    ui.input("제목 (EN)", value=c["title_en"],
                             on_change=lambda e, cc=c: cc.update(title_en=e.value)) \
                        .props("dense outlined").classes("w-full")
            ui.button("저장 · 미리보기 재렌더", icon="refresh",
                      on_click=lambda: ui.notify(
                          "미리보기 탭을 다시 열면 갱신됩니다 (시연 · 파일 생성 없음)",
                          color="positive")).props("color=primary").classes("q-mt-sm")
        with ui.tab_panel("kr"):
            # KR/EN 은 탭을 하나 더 두지 않고 토글로 바꾼다.
            lang = ui.toggle(["KR", "EN"], value="KR").props("dense no-caps")

            @ui.refreshable
            def nl() -> None:
                code = "kr" if lang.value == "KR" else "en"
                with ui.row().classes("items-center gap-2 q-mb-xs"):
                    ui.label("newsletter_" + code + ".html").classes(
                        "text-caption").style("font-family:ui-monospace,monospace")
                    ui.label("실제 발송 템플릿으로 렌더한 결과").classes(
                        "text-caption text-grey-7")
                    ui.space()
                    ui.link("새 창으로", "/demo/newsletter/" + code,
                            new_tab=True).classes("text-caption")
                _iframe("/demo/newsletter/" + code, "58vh")

            lang.on_value_change(lambda: nl.refresh())
            nl()

    ui.separator().classes("q-mt-sm")
    with ui.row().classes("items-center gap-2 q-mt-sm"):
        ui.label("발송").classes("text-subtitle2")
        ui.badge("Submission — 제작만", color="teal-7")
        ui.label("발송은 Approval 갈래에서 이 승인본을 그대로 씁니다(다시 만들지 않습니다).") \
            .classes("text-caption text-grey-7")

    def _send() -> None:
        with ui.dialog() as dlg, ui.card().style("min-width:520px"):
            ui.label("Mailchimp 발송 확인").classes("text-subtitle1")
            ui.separator()
            _mono("대상 태그(세그먼트)   PM95-Android15\n"
                  "수신자                412명\n"
                  "제목(KR)              PM95 (Android 15) 95.52 업데이트 안내\n"
                  "제목(EN)              PM95 (Android 15) 95.52 Update")
            ui.label("발송은 되돌릴 수 없습니다. 대상과 수신자 수를 확인하세요.") \
                .classes("text-caption q-mt-sm").style("color:#b3261e")
            with ui.row().classes("justify-end w-full q-mt-md gap-2"):
                ui.button("취소", on_click=dlg.close).props("flat")
                ui.button("발송", on_click=lambda: (
                    dlg.close(), ui.notify("시연 화면이라 발송하지 않았습니다", color="warning"))) \
                    .props("color=negative")
        dlg.open()

    ui.button("Mailchimp 발송", icon="campaign", on_click=_send) \
        .props("outline color=negative").classes("q-mt-xs")


def _doc_page(title: str, body: str) -> str:
    """문서 미리보기용 최소 HTML 껍데기 — 실제 페이지의 글꼴·여백 느낌만 맞춘다."""
    return (
        "<html><head><meta charset='utf-8'><style>"
        "body{margin:0;padding:20px 24px;background:#fff;color:#172b4d;"
        "font-family:-apple-system,'Segoe UI',Roboto,'Malgun Gothic',sans-serif;"
        "font-size:13px;line-height:1.6}"
        "h1{font-size:19px;margin:0 0 4px;color:#172b4d}"
        "h2{font-size:16px;margin:18px 0 6px;border-bottom:1px solid #dfe1e6;"
        "padding-bottom:4px}"
        "h3{font-size:13px;margin:14px 0 4px;color:#42526e}"
        "table{border-collapse:collapse;width:100%;margin:6px 0 14px}"
        "th,td{border:1px solid #dfe1e6;padding:6px 8px;text-align:left;"
        "vertical-align:top}"
        "th{background:#f4f5f7;font-weight:600;color:#42526e}"
        "a{color:#0052cc;text-decoration:none}"
        "ul{margin:6px 0 12px;padding-left:20px}"
        ".muted{color:#6b778c;font-size:12px}"
        ".panel{border:1px solid #dfe1e6;border-radius:3px;padding:10px 12px;"
        "background:#fafbfc;margin:8px 0 14px}"
        "</style></head><body>" + body + "</body></html>")


def _sp_doc_html() -> str:
    """Service Portal 'Resources (OS, SDK) & Document' 문서 — 실제 문서 구성 그대로.

    상단 현재 버전 표기, OS Image 표(Full/Incremental 과 'Other versions' 열),
    Release Note·SDK 링크가 실제 문서가 가진 요소다. 시스템은 이 자리들만 찾아
    바꾼다.
    """
    return _doc_page("PM95 A15 Resources", (
        "<h1>PM95 A15 Resources (OS, SDK) &amp; Document</h1>"
        "<p class='muted'>Latest OS version &nbsp;<b>95.52 (Jul 20 2026)</b></p>"
        "<h2>OS Image</h2>"
        "<table><tr><th style='width:22%'>Type</th><th style='width:38%'>File</th>"
        "<th>Other versions</th></tr>"
        "<tr><td>Full Update</td>"
        "<td><a href='#'>PM95_A15_95.52_sdcard.zip</a></td>"
        "<td>Other versions &rarr; 95.52 (Jul 20 2026)</td></tr>"
        "<tr><td>Incremental Update</td>"
        "<td><a href='#'>PM95_A15_95.51_to_95.52_sdcard.zip</a></td>"
        "<td>95.51 (Mar 31 2026) &rarr; 95.52 (Jul 20 2026)</td></tr></table>"
        "<h2>Document</h2>"
        "<ul>"
        "<li><a href='#'>Release Note</a></li>"
        "<li><a href='#'>SDK</a> - v3.41.09</li>"
        "</ul>"
        "<p class='muted'>Android security patch level : July 5, 2026</p>"))


def _kb_doc_html() -> str:
    """Knowledge Base 문서 — Confluence 에 올라간 뒤 보이는 모습.

    버전 제목 아래 SDK·보안 패치·OS 내려받기 링크가 오고, 'Release Notes' 안에
    항목이 분류별 표로 들어간다. 시스템은 이 구조 그대로 새 버전 블록을 만든다.
    """
    rows = {}
    for kind, mod, text in _KB_ITEMS:
        rows.setdefault(mod, {"NEW": [], "IMPROVED": [], "FIXED": []})[kind].append(text)
    body = [
        "<h1>PM95 Android 15 — Release Notes</h1>",
        "<h2>95.52 (Jul 20 2026)</h2>",
        "<ul><li><b>SDK: v3.41.09</b></li>",
        "<li><i>This build is compliant up to Android Security Bulletin of "
        "<a href='#'>Jul 05, 2026</a>.</i></li>",
        "<li><a href='#'>OS Download Link</a></li></ul>",
        "<div class='panel'><b>Release Notes</b>",
    ]
    for mod, kinds in rows.items():
        body.append("<h3>" + mod + "</h3><table>")
        for kd in ("NEW", "IMPROVED", "FIXED"):
            if not kinds[kd]:
                continue
            body.append("<tr><th style='width:16%'>" + kd + "</th><td>"
                        + "<br>".join(kinds[kd]) + "</td></tr>")
        body.append("</table>")
    body.append("</div>")
    body.append("<p class='muted'>95.51 (Mar 31 2026) — 이전 버전 블록은 그대로 "
                "남습니다.</p>")
    return _doc_page("KB", "".join(body))


def _kb_edit_html() -> str:
    """KB 편집기에 처음 들어가는 내용 — **이번 버전 블록**만.

    위 미리보기(_kb_doc_html)와 같은 _KB_ITEMS 를 쓴다. 실제 편집도 이번 버전 블록만
    새로 쓰고 이전 버전 블록은 건드리지 않으므로, 편집기에도 그 부분만 올린다.
    """
    rows: dict = {}
    for kind, mod, text in _KB_ITEMS:
        rows.setdefault(mod, {"NEW": [], "IMPROVED": [], "FIXED": []})[kind].append(text)
    out = [
        "<h2>95.52 (Jul 20 2026)</h2>",
        "<ul><li><b>SDK: v3.41.09</b></li>",
        "<li><i>This build is compliant up to Android Security Bulletin of "
        "Jul 05, 2026.</i></li>",
        '<li><a href="#">OS Download Link</a></li></ul>',
        "<p><b>Release Notes</b></p>",
    ]
    for mod, kinds in rows.items():
        out.append("<h3>" + mod + "</h3>")
        for kd in ("NEW", "IMPROVED", "FIXED"):
            if kinds[kd]:
                out.append("<p><b>" + kd + "</b></p><ul>"
                           + "".join("<li>" + t + "</li>" for t in kinds[kd])
                           + "</ul>")
    return "".join(out)


def _review_portal(S: dict, kind: str, *, upload: bool = False) -> None:
    """⑤⑥ 검수 — 편집 결과 리포트 + 에디터.

    upload=True 면 [로컬 저장] 옆에 [업로드] 를 하나 더 둔다. Approval 의 Knowledge
    Base 업로드가 그 경우다 — 화면은 Submission 의 KB 편집과 같고, 실제 문서에
    올리는 버튼만 더 있다(사용자 확정 2026-08-28).
    """
    with ui.card().classes("w-full bg-green-1 q-mb-sm"):
        ui.label("✔ 편집·저장은 로컬에만 반영됩니다. 실제 문서는 '검수 완료' 시에만 교체됩니다.") \
            .classes("text-caption text-green-10")
    if kind == "sp":
        with ui.card().classes("w-full bg-blue-1"):
            ui.label("편집 결과 — 적용 " + str(len(_SP_CHANGES)) + "건 · 미적용 "
                     + str(len(_SP_FAILED)) + "건 (아래 에디터에 반영됨)").classes("text-subtitle2")
            for c in _SP_CHANGES:
                with ui.column().classes("gap-0 q-mb-xs"):
                    ui.label("✔ " + c["why"]).classes("text-caption text-green-10")
                    ui.label("− " + c["old"]).classes("text-caption text-red-9").style(
                        "white-space:pre-wrap; word-break:break-all")
                    ui.label("+ " + c["new"]).classes("text-caption text-green-9").style(
                        "white-space:pre-wrap; word-break:break-all")
            for f in _SP_FAILED:
                ui.label("✘ " + f).classes("text-caption text-orange-9")
        ui.separator().classes("q-my-sm")
        ui.label("문서 미리보기 — 고객 지원 사이트에 올라가 있는 그 문서입니다") \
            .classes("text-caption text-grey-7")
        _srcdoc(_sp_doc_html(), "34vh")
        ui.separator().classes("q-my-sm")
        ui.label("문서 #14827 — PM95 Android 15 · Resources").classes(
            "text-caption text-grey-7")
        ui.textarea(value=(
            "PM95 (Android 15) — Resources\n\n"
            "Latest OS version : 95.52 (Jul 20 2026)\n"
            "Android security patch level : July 5, 2026\n"
            "EmKit SDK : 3.41.09\n\n"
            "Downloads\n"
            "  · OS image (sdcard)      PM95_A15_95.52_sdcard.zip\n"
            "  · Flash tool             PM95_A15_95.52_FlashTool.zip\n"
            "  · Release Note           PM95_A15_Release_Note_95.52 for Sales.pdf")) \
            .props("dense outlined autogrow").classes("w-full").style(
                "font-family:ui-monospace,monospace; font-size:12.5px")
        ui.label("실제 화면에서는 이 자리가 SmartEditor2(Service Portal 편집기)입니다.") \
            .classes("text-caption text-grey-6")
    else:
        ui.label("항목을 VoC/EmKit/OS 별로, NEW/IMPROVED/FIXED 로 분류한 결과입니다.") \
            .classes("text-caption text-grey-7")
        COLOR = {"NEW": "green-7", "IMPROVED": "blue-7", "FIXED": "grey-6"}
        for kd, mod, text in _KB_ITEMS:
            with ui.row().classes("items-center gap-2 no-wrap q-py-xs").style(
                    "border-bottom:1px solid #eceeed"):
                ui.badge(kd, color=COLOR[kd])
                ui.badge(mod, color="blue-grey-5").props("outline")
                ui.label(text).classes("text-caption").style(
                    "overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0")
        ui.separator().classes("q-my-sm")
        ui.label("문서 미리보기 — 기술 지원 문서에 이 모양으로 실립니다") \
            .classes("text-caption text-grey-7")
        _srcdoc(_kb_doc_html(), "40vh")
        ui.separator().classes("q-my-sm")
        # 예전에는 이 자리에 문서 내용을 **글자로만** 떠 놓았다(고정폭 상자). 편집하는
        # 단계인데 고칠 수 있는 것이 없어 보였다 — 실제 화면처럼 편집기를 둔다.
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.label("이번 버전 블록 편집").classes("text-caption").style("font-weight:700")
            ui.badge("95.52 (Jul 20 2026)", color="blue-grey-6")
            ui.label("고친 내용이 위 문서 미리보기에 들어갑니다") \
                .classes("text-caption text-grey-7")
        kb_editor = ui.editor(value=_kb_edit_html()) \
            .classes("w-full").style("min-height:20rem")
        kb_editor._props["toolbar"] = [
            ["bold", "italic", "underline", "strike"],
            ["h2", "h3"],
            ["unordered", "ordered"],
            ["link"],
            ["removeFormat"],
            ["undo", "redo"],
            ["viewsource"],
        ]
        ui.label("실제 화면에서는 이 자리가 TinyMCE(Confluence 편집용)입니다. 서식은 위 "
                 "버튼으로 넣고, 맨 오른쪽 </> 을 누르면 HTML 원문을 직접 고칩니다.") \
            .classes("text-caption text-grey-6")
    with ui.row().classes("items-center gap-2 q-mt-sm"):
        ui.button("로컬 저장", icon="save",
                  on_click=lambda: ui.notify(
                      "검수 작업공간에만 저장했습니다 — 실제 문서는 그대로입니다 (시연)",
                      color="primary", multi_line=True)).props("color=primary")
        if upload:
            ui.button("업로드", icon="cloud_upload",
                      on_click=lambda: ui.notify(
                          "Knowledge Base 에 올렸습니다 — 시연이라 실제 문서는 "
                          "바뀌지 않습니다",
                          color="positive", multi_line=True)) \
                .props("outline color=primary") \
                .tooltip("검수를 마친 내용을 실제 Knowledge Base 문서에 반영합니다")


def _mail_dialog(m: dict) -> None:
    """근거 메일 한 통을 실제 메일 화면처럼 띄운다(제목·보낸사람·받은 시각·본문)."""
    with ui.dialog() as dlg, ui.card().classes("w-full").style(
            "max-width:60rem; max-height:88vh; overflow-y:auto"):
        with ui.row().classes("items-start gap-2 w-full no-wrap"):
            ui.label(m["subject"]).classes("text-subtitle1").style(
                "white-space:normal; word-break:break-word; flex:1")
            ui.button(icon="close", on_click=dlg.close).props("flat round dense")
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.badge("원문" if m.get("original") else "회신",
                     color="blue-grey-6" if m.get("original") else "grey-5")
            ui.label(m.get("sender", "")).classes("text-caption text-grey-7")
            ui.space()
            ui.label(m.get("when", "")).classes("text-caption text-grey-6")
        ui.separator()
        _mono(m.get("body", "(본문 없음)"))
        with ui.row().classes("w-full justify-end q-mt-sm"):
            ui.button("닫기", on_click=dlg.close).props("flat")
    dlg.open()


def _basis_mails() -> None:
    """이 회차를 '신규'로 판단한 근거 메일.

    실제 화면은 접어 두지만 시연에서는 **펼친 채로** 연다 — 메일 한 통이 회차를
    깨우는 출발점이라, 그것부터 보여 줘야 흐름이 이어진다(사용자 확정 2026-08-21).
    """
    with ui.expansion(value=True).classes("w-full q-mb-sm").props(
            "dense expand-separator") as e:
        with e.add_slot("header"):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.icon("mail_outline", size="sm").classes("text-grey-6")
                ui.label("근거 메일 " + str(len(_TRIGGER_MAILS)) + "건").classes(
                    "text-caption").style("font-weight:700")
                ui.label("회신 " + str(_TRIGGER_REPLIES) + "건 제외").classes(
                    "text-caption text-grey-6")
        for m in _TRIGGER_MAILS:
            with ui.row().classes("w-full items-center gap-2 no-wrap").style(
                    "padding:2px 6px"):
                not_re = m["original"]
                ui.badge("원문" if not_re else "회신",
                         color="blue-grey-6" if not_re else "grey-5").props(
                    "" if not_re else "outline")
                ui.label(m["subject"]).classes("text-caption").style(
                    "overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                    " min-width:0")
                ui.space()
                ui.label(m["when"]).classes("text-caption text-grey-6").style("flex:none")
                # 메일 내용은 **그 메일 줄에서** 연다. 예전에는 카드 아래에 버튼 하나만
                # 있어서 어느 메일을 여는 것인지 알 수 없었다(사용자 지적 2026-08-28).
                ui.button("메일 내용 보기", icon="open_in_new",
                          on_click=lambda _e=None, mm=m: _mail_dialog(mm)) \
                    .props("flat dense no-caps size=sm color=primary") \
                    .style("flex:none")


def _os_pipeline(S: dict) -> None:
    """OS Update — 메일로 감지된 회차 하나를 Submission·Approval 두 갈래로 처리한다."""
    _explain(
        "OS 새 버전 하나를 고객에게 내보내기까지 필요한 열 가지 일을 한 화면에서 "
        "끝냅니다. 담당자가 사내 파일 서버, 배포 사이트, 고객 지원 사이트, 안내 "
        "메일을 오가며 처리하던 것을 하나의 흐름으로 묶어, 담당자가 바뀌어도 같은 "
        "순서와 같은 품질로 나가게 합니다. 각 단계는 사람이 확인한 뒤에만 다음으로 "
        "넘어갑니다.",
        [
            "고객 안내 문서 작성 — 이번 버전에 들어간 개선 사항을 모두 읽어 고객에게 "
            "알릴 내용만 가려내고, 문서에 실을 문장으로 다듬어 완성합니다. 담당자가 "
            "개선 건을 하나씩 확인하고 문구를 직접 쓰던 일을 대신합니다.",
            "사양 정보 채우기 — 이전 버전 문서에 적혀 있던 사양 정보를 가져와 이번 "
            "버전에 맞게 고쳐 넣습니다. 사람이 옮겨 적다 틀리던 일을 없앱니다.",
            "고객 지원 사이트 갱신 — 이미 올라가 있는 안내 문서에서 이번 버전에 맞춰 "
            "바꿔야 할 문장만 찾아 고칩니다. 문서를 새로 쓰지 않아 기존 내용이 그대로 "
            "남습니다.",
            "기술 지원 문서 정리 — 이번에 바뀐 내용을 새 기능·개선·오류 수정으로 나눠 "
            "정리합니다.",
            "고객 공지 메일 문안 — 알리기로 한 내용만으로 한국어·영어 안내문을 "
            "만듭니다. 확인되지 않은 내용은 넣지 않습니다.",
        ])

    # 이 회차가 왜 생겼는지가 먼저다 — 실제 화면(os_update._todo_panel)도
    # 근거 메일을 패널 맨 처음에 건다.
    _basis_mails()

    live: dict = {"n": 0, "t": 0}

    with ui.card().classes("w-full q-mb-sm"):
        with ui.row().classes("items-center gap-3 w-full no-wrap"):
            ui.label("작업").classes("text-caption text-grey-7")
            ui.select(["PM95 · Android 15 · GMS  —  95.52",
                       "PM45 · Android 9 · GMS  —  45.81",
                       "MF52E · Android 15 · GMS  —  MF52EG15.00"],
                      value="PM95 · Android 15 · GMS  —  95.52") \
                .props("dense outlined").style("width:330px")
            ui.space()
            ui.badge("완료", color="green-8")
        with ui.row().classes("gap-6 q-mt-xs").style("flex-wrap:wrap"):
            for lab, val in [("제품", "PM95 Android 15"), ("버전", "95.52"),
                             ("빌드일", "2026-07-20"), ("출시", "Jul 20 2026")]:
                with ui.column().classes("gap-0"):
                    ui.label(lab).classes("text-caption text-grey-7")
                    ui.label(val).classes("text-body2")

    def _run(st: dict) -> None:
        live["n"] = 0
        live["t"] = 0
        st["status"] = "running"
        board.refresh()
        ui.notify("[" + st["label"] + "] 실행을 시작했습니다", color="primary")

    def _cancel(st: dict) -> None:
        st["status"] = "pending"
        board.refresh()
        ui.notify("중단했습니다", color="warning")

    def _set(st: dict, status: str) -> None:
        st["status"] = status
        board.refresh()

    def _review(st: dict) -> None:
        # 실제 검수 팝업과 같은 크기(task_board) — 전체화면이 아니라 넓은 카드다.
        with ui.dialog() as dlg, ui.card().classes("w-full").style(
                "max-width:104rem; max-height:92vh; overflow-y:auto"):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(st["label"] + " · 검수").classes("text-subtitle1")
                lab, col = _STATUS[st["status"]]
                ui.badge(lab, color=col)
                if st["ai"]:
                    ui.badge("AI", color="amber-9").props("outline")
                ui.space()
                ui.label("실행자 " + (st["by"] or "-")).classes("text-caption text-grey-6")
                ui.button(icon="close", on_click=dlg.close).props("flat round dense")
            ui.separator()
            with ui.column().classes("w-full"):
                _reuse = st["side"] == "approval" and st["key"] in _APPROVAL_LABEL
                if st["key"] == "release_note":
                    _review_rn(S, st)
                elif st["key"] == "release_note_edit":
                    # Submission(KB 편집)·Approval(KB 업로드)이 **같은 화면**을 쓴다.
                    # Approval 에는 [업로드] 버튼이 하나 더 붙는다(2026-08-28).
                    _review_portal(S, "kb", upload=(st["side"] == "approval"))
                elif _reuse:
                    ui.label("Submission 승인본을 그대로 씁니다 — 다시 만들지 않습니다.") \
                        .classes("text-caption").style("color:#8a5a12")
                    _mono(st["result"])
                elif st["key"] == "newsletter":
                    _review_newsletter(S)
                elif st["key"] == "service_portal":
                    _review_portal(S, "sp")
                elif st["key"] == "release_note_edit":
                    _review_portal(S, "kb")
                else:
                    _mono(st["result"])
                    if st["key"] == "dropbox_cleanup":
                        ui.label("삭제 버튼은 경로별로 여기에 붙습니다.") \
                            .classes("text-caption q-mt-sm").style("color:#b3261e")

            def _ok() -> None:
                st["status"] = "done"
                board.refresh()
                dlg.close()
                ui.notify("[" + st["label"] + "] 검수 완료 처리했습니다", color="positive")

            with ui.row().classes("justify-end w-full q-mt-sm gap-2"):
                ui.button("닫기", on_click=dlg.close).props("flat")
                if st["status"] != "done":
                    ui.button("검수 완료", on_click=_ok).props("color=primary")
        dlg.open()

    def _side(side: str, edge: str, head: str) -> None:
        """갈래 하나 — 실제 task_board.render_side 와 같은 구성."""
        stages = S["stages"][side]
        done = sum(1 for s in stages if s["status"] == "done")
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.label(side.capitalize()).classes("text-subtitle2").style(
                "color:" + head + "; font-weight:700")
            ui.label("2026-07-16").classes("text-caption text-grey-6")
            ui.space()
            ui.badge(str(done) + "/" + str(len(stages)),
                     color="green-8" if done == len(stages) else "grey-6")

        nxt = next((s for s in stages if s["status"] == "pending"), None)
        busy = any(s["status"] == "running" for s in stages)
        with ui.row().classes("w-full items-center gap-2 q-mt-xs"):
            if nxt is not None and not busy:
                first = all(s["status"] == "pending" for s in stages)
                ui.button("처리 시작 (1단계 실행 승인)" if first else "다음 단계 실행 승인",
                          on_click=lambda s=nxt: _run(s)).props("size=sm") \
                    .tooltip("승인하면 이 단계를 Claude가 도구를 사용해 수행합니다.")
                if side == "approval":
                    ui.button("전체 실행", icon="playlist_play",
                              on_click=lambda s=nxt: _run(s)) \
                        .props("size=sm outline color=primary") \
                        .tooltip("모든 단계를 순차로 실행합니다"
                                 "(각 단계는 검수 대기로 남습니다)")
            # '메일 내용 보기' 는 이 카드가 아니라 **위쪽 근거 메일 목록의 각 줄**에 있다
            # (2026-08-28). 여기 있을 때는 어느 메일을 여는 것인지 알 수 없었다.

        ui.separator().classes("q-my-xs")
        for st in stages:
            _stage_row(st)

    def _stage_row(st: dict) -> None:
        label, color = _STATUS[st["status"]]
        # 실제 작업 보드(task_board._render_stage_row)와 같은 줄 구성이다.
        with ui.row().classes("w-full items-center gap-2 no-wrap py-1"):
            ui.label(st["label"]).classes("text-body2").style("min-width:150px")
            ui.badge(label, color=color)
            if st["ai"]:
                ui.badge("AI", color="amber-9").props("outline")
            if st["status"] == "running":
                ui.spinner(size="sm")
                msg = ui.label("시작하는 중…").classes(
                    "text-caption text-grey-7 ellipsis flex-grow").style("min-width:0")

                def _tick(lbl=msg, s=st) -> None:
                    if s["status"] != "running":
                        return
                    live["t"] += 1
                    steps = s["steps"]
                    idx = min(live["n"], len(steps) - 1)
                    mm, ss = divmod(live["t"], 60)
                    et = (str(mm) + "분 " + format(ss, "02d") + "초") if mm \
                        else (str(ss) + "초")
                    lbl.set_text(steps[idx] + "  ·  " + et)
                    if live["t"] % 2 == 0:
                        live["n"] += 1
                    if live["n"] >= len(steps):
                        s["status"] = "review"
                        board.refresh()
                        ui.notify("[" + s["label"] + "] 실행이 끝나 검수 대기로 "
                                  "넘어갔습니다", color="warning")

                ui.timer(1.0, _tick)
                ui.button("중단", icon="stop", on_click=lambda s=st: _cancel(s)) \
                    .props("flat dense size=sm color=negative")
            else:
                if st["by"]:
                    ui.label("· " + st["by"]).classes("text-caption text-grey-6")
                ui.space()
                if st["status"] == "pending":
                    ui.button("실행 승인", on_click=lambda s=st: _run(s)) \
                        .props("outline size=sm color=primary")
                elif st["status"] == "review":
                    ui.button("검수하기", on_click=lambda s=st: _review(s)) \
                        .props("color=orange-8 size=sm")
                    ui.button("되돌리기", on_click=lambda s=st: _set(s, "pending")) \
                        .props("flat size=sm")
                else:
                    ui.button("검수 결과 보기", on_click=lambda s=st: _review(s)) \
                        .props("flat size=sm color=grey-7")
                    ui.button("되돌리기", on_click=lambda s=st: _set(s, "review")) \
                        .props("flat size=sm")

    @ui.refreshable
    def board() -> None:
        # 실제 화면(os_update._todo_panel)과 같이 두 갈래를 나란히 둔다.
        with ui.row().classes("w-full no-wrap items-stretch gap-4"):
            for side, edge, head in (("submission", "#4A7C67", "#3C6553"),
                                     ("approval", "#d99a3a", "#8a5a12")):
                with ui.column().classes("flex-1 min-w-0"):
                    with ui.card().classes("w-full h-full").style(
                            "border-left:5px solid " + edge):
                        _side(side, edge, head)

    board()


# ══════════════════════════════════════════════════════════════════════════
# WhiteList
# ══════════════════════════════════════════════════════════════════════════


def _wl_mailbox(S: dict) -> None:
    _explain(
        "고객사가 보내오는 단말 등록 요청 메일을 한곳에 모으고, 처리할 요청인지와 대상 단말을 가려냅니다. 메일을 일일이 열어 읽고 단말 번호를 "
        "옮겨 적던 일을 없애 접수부터 처리까지의 시간을 줄이고 누락을 막습니다.",
        [
            "요청 판별 — 메일과 첨부를 읽어 처리할 요청인지, 새로 등록하는 건인지 이미 등록된 단말을 바꾸는 건인지 가려냅니다. 담당자가 "
            "메일을 열어 하나씩 읽던 일을 대신합니다.",
            "대상 확인 — 제품과 올릴 OS 버전, 등록할 단말 번호를 주고받은 메일 전체에서 모읍니다. 사람이 옮겨 적다 빠뜨리던 단말을 "
            "없앱니다.",
            "판단 근거 제시 — 왜 그렇게 판단했는지 함께 보여 줘 담당자가 바로 확인하고 처리할 수 있게 합니다.",
        ])

    @ui.refreshable
    def threads() -> None:
        for m in S["mails"]:
            with ui.card().classes("w-full q-mb-sm"), ui.expansion().classes("w-full") as exp:
                with exp.add_slot("header"), ui.row().classes("items-center gap-2 w-full no-wrap"):
                    if m["state"] == "done":
                        ui.badge("처리 후", color="green-7").tooltip("등록 완료")
                    elif m["state"] == "new":
                        ui.badge("새 회신", color="deep-orange-8") \
                            .tooltip("이 대화는 처리 완료로 표시했지만, 그 뒤에 새 메일이 왔습니다.")
                    else:
                        ui.badge("처리 전", color="orange-8")
                    ui.label(m["subject"]).classes("text-weight-medium flex-1 min-w-0 ellipsis")
                    if m["count"] > 1:
                        ui.badge("메일 " + str(m["count"]), color="blue-grey-5").props("outline")
                    ui.badge("SN " + str(len(m["sns"])), color="blue-7").props("outline")
                    if m["att"]:
                        ui.badge("📎 첨부", color="deep-purple-5").props("outline").tooltip(m["att"])
                    ui.label(m["when"]).classes("text-caption text-grey-6")

                ui.label("보낸사람: " + m["sender"]).classes("text-caption text-grey-7")
                if m["att"]:
                    ui.label("첨부: " + m["att"]).classes("text-caption text-deep-purple-8")
                info = "모델 " + m["model"] + " · OS " + m["from_os"] + " → " + m["to_os"]
                ui.label("Claude 판정: " + info).classes("text-caption text-grey-8")
                with ui.row().classes("items-center gap-2"):
                    ui.badge(m["verdict"], color="amber-9").props("outline")
                    ui.label("신뢰도 " + format(m["conf"], ".2f")).classes("text-caption text-grey-7")
                ui.label(m["reason"]).classes("text-caption text-grey-7")

                dups = sum(1 for _s, d in m["sns"] if d)
                ui.label("일련번호").classes("text-caption text-weight-bold q-mt-sm")
                with ui.column().classes("gap-0"):
                    for sn, d in m["sns"]:
                        with ui.row().classes("items-center gap-2 no-wrap"):
                            ui.checkbox(sn, value=(d is None)).props("dense")
                            if d:
                                ui.label("이미 신청됨 · " + d["request_id"] + " ("
                                         + d["created_at"] + ", " + d["status"] + ")") \
                                    .classes("text-caption text-orange-9")
                if dups:
                    ui.label("※ " + str(dups) + "건은 이미 신청되어 체크를 해제했습니다.") \
                        .classes("text-caption text-grey-6")

                with ui.row().classes("gap-2 q-mt-sm"):
                    if m["state"] != "done":
                        ui.button("처리하기", icon="app_registration",
                                  on_click=lambda x=m: _process_dialog(x)) \
                            .props("size=sm color=primary")
                        ui.button("처리 완료", icon="done", on_click=lambda x=m: _mark(x)) \
                            .props("flat dense size=sm color=grey-7")
                    else:
                        ui.button("다시 열기", icon="undo", on_click=lambda x=m: _unmark(x)) \
                            .props("flat dense size=sm color=grey-7")

    def _mark(m: dict) -> None:
        m["state"] = "done"
        threads.refresh()
        ui.notify("처리 완료로 표시했습니다 (시연)", color="positive")

    def _unmark(m: dict) -> None:
        m["state"] = "open"
        threads.refresh()

    def _process_dialog(m: dict) -> None:
        """처리 팝업 — 실제 화면과 같이 ① 처리 · ② 확인 · ③ 메일 회신 세 단계다.

        등록을 실행하면 확인 탭으로, 확인이 끝나면 회신 탭으로 넘어간다.
        """
        is_date = m["verdict"] == "등록일 변경"
        with ui.dialog() as dlg, ui.card().classes("w-full").style(
                "max-width:1000px; max-height:92vh"):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label("처리 · " + m["subject"]).classes("text-subtitle1").style(
                    "overflow:hidden; text-overflow:ellipsis; white-space:nowrap")
                ui.badge(m["verdict"], color="amber-9" if is_date else "green-8") \
                    .props("outline")
                ui.space()
                ui.button(icon="close", on_click=dlg.close).props("flat round dense")
            ui.separator()

            with ui.tabs().props("dense no-caps align=left").classes("w-full") as tabs:
                t_reg = ui.tab("① 처리", icon="app_registration")
                t_check = ui.tab("② 확인", icon="fact_check")
                t_reply = ui.tab("③ 메일 회신", icon="reply")

            with ui.tab_panels(tabs, value=t_reg).classes("w-full").style(
                    "max-height:74vh; overflow-y:auto"):
                # ── ① 처리 ─────────────────────────────────────────
                with ui.tab_panel(t_reg):
                    if is_date:
                        _panel_date(m, lambda: tabs.set_value(t_check))
                    else:
                        _panel_register(m, lambda: tabs.set_value(t_check))
                # ── ② 확인 ─────────────────────────────────────────
                with ui.tab_panel(t_check):
                    ui.label("보낸 뒤 FOTA 에서 다시 읽어 기대값과 맞는지 대조합니다. "
                             "'처리했다'는 응답만 믿고 회신하지 않기 위한 단계입니다.") \
                        .classes("text-caption text-grey-7 q-mb-sm")
                    _wl_verify(S, m)
                    with ui.row().classes("w-full justify-end q-mt-sm"):
                        ui.button("회신 단계로", icon="arrow_forward",
                                  on_click=lambda: tabs.set_value(t_reply)) \
                            .props("unelevated dense color=primary")
                # ── ③ 메일 회신 ────────────────────────────────────
                with ui.tab_panel(t_reply):
                    _wl_reply(S, m, on_sent=lambda: (_mark(m), dlg.close()))
        dlg.open()

    def _panel_register(m: dict, go_next) -> None:
        n = len(m["sns"])
        dup = sum(1 for _s, d in m["sns"] if d)
        ui.label("FOTA 실시간 조회 결과").classes("text-caption text-grey-7")
        _mono("대상 SN        " + str(n) + "대\n"
              "신규 등록      " + str(n - dup) + "대\n"
              "이미 등록됨    " + str(dup) + "대  → 등록일 변경 구역으로 분리됩니다\n"
              "적용 규칙      Android " + str(m.get("from_android") or "11")
              + " → " + str(m.get("to_android") or "13"))
        # 화이트리스트 규칙은 **Android 버전** 으로 정해진다(wl_rules.from_os/to_os
        # 가 11 → 13 같은 정수다). OS 빌드 버전이 아니다.
        _AV = ["8", "9", "11", "13", "15"]
        _cur = str(m.get("from_android") or "11")
        _to = str(m.get("to_android") or "13")
        with ui.row().classes("items-center gap-2 q-mt-sm"):
            ui.select(["PM86", "PM95", "PM45"],
                      value=m["model"].split()[0] if m["model"] else "PM86",
                      label="모델").props("dense outlined").style("width:130px")
            ui.select(_AV, value=(_cur if _cur in _AV else _AV[2]),
                      label="현재 Android (from)").props("dense outlined") \
                .style("width:180px")
            ui.select(_AV, value=(_to if _to in _AV else _AV[3]),
                      label="올릴 Android (to)").props("dense outlined") \
                .style("width:180px")
            ui.select(["신규", "갱신"], value="신규", label="구분") \
                .props("dense outlined").style("width:110px")
        ui.label("모델과 Android 버전 조합으로 등록 규칙이 정해집니다 — 이 단말들은 "
                 "현재 Android 에서 올릴 Android 로만 업데이트를 받습니다.") \
            .classes("text-caption text-grey-7")

        def _go() -> None:
            ui.notify("등록 완료 — 확인 단계로 넘어갑니다", color="positive")
            go_next()

        with ui.row().classes("justify-end w-full q-mt-md gap-2"):
            ui.button("실행", on_click=_go).props("color=primary")

    def _panel_date(m: dict, go_next) -> None:
        ui.label("AI 해석 결과").classes("text-caption text-grey-7")
        _mono("요청 유형   등록일 변경 (신뢰도 0.95)\n"
              "요청 날짜   2026-08-18\n"
              "대상 SN     11대 · PM45 1 · PM86 7 · PM95 2 · PM90 1\n"
              "근거        본문 + 첨부 메일 3건")
        with ui.row().classes("items-center gap-3 q-mt-sm"):
            ui.input("변경할 등록일", value="2026-08-18").props("dense outlined") \
                .style("width:190px")
            ui.label("지원 종료일은 등록일 + 1년으로 함께 바뀝니다").classes(
                "text-caption text-grey-7")
        # 바뀔 값은 눌러서 여는 게 아니라 처음부터 보인다 — 실행 전에 무엇이
        # 어떻게 바뀌는지가 화면에 이미 있어야 확인하고 누를 수 있다.
        ui.label("현재 값 → 바뀔 값").classes("text-caption text-grey-7 q-mt-sm")
        cols = [{"name": "sn", "label": "sn", "field": "sn", "align": "left"},
                {"name": "cur", "label": "현재 등록일", "field": "cur",
                 "align": "left"},
                {"name": "new", "label": "바뀔 등록일", "field": "new",
                 "align": "left"},
                {"name": "end", "label": "supportEndDate", "field": "end",
                 "align": "left"}]
        data = [{"sn": s, "cur": "2026-05-02", "new": "2026-08-18",
                 "end": "2027-08-18"} for s, _d in m["sns"]]
        ui.table(columns=cols, rows=data, row_key="sn").classes("w-full") \
            .props("dense flat")
        ui.label("등록되지 않은 SN 이 하나라도 있으면 아무것도 바꾸지 않고 전부 "
                 "중단합니다 (한 트랜잭션 · 행 수가 안 맞으면 롤백).") \
            .classes("text-caption").style("color:#b3261e")

        def _apply() -> None:
            ui.notify("등록일 변경 완료 — 확인 단계로 넘어갑니다", color="positive")
            go_next()

        with ui.row().classes("justify-end w-full q-mt-md gap-2"):
            ui.button("등록일 변경 실행", on_click=_apply).props("color=primary")

    threads()


def _wl_reply(S: dict, m: dict, on_sent=None) -> None:
    """WhiteList — ③ 메일 회신. 실제 화면과 같은 구성이다.

    제목과 본문은 실제 초안 생성기(whitelist.mailer.build_reply_draft)가 만드는
    문구 그대로다 — 시연에서 보이는 문안이 진짜 나가는 문안과 같다.

    단말 목록은 **그 메일의 SN 그대로** 넣는다 — ① 처리에서 안내한 것과 같아야 한다
    (예전에는 메일과 무관한 4대가 박혀 있었다, 사용자 지적 2026-08-28).
    등록일 변경 건이면 kind 를 넘겨 '등록되었습니다' 가 아닌 날짜 변경 문구로 만든다.
    """
    from app.whitelist.mailer import build_reply_draft

    devices = [{"sn": s} for s, _d in (m.get("sns") or [])]
    date_only = m.get("verdict") == "등록일 변경"
    draft = build_reply_draft(
        to_name="TA", devices=devices, lang="ko",
        kind=("등록일 변경" if date_only else ""),
        new_date=("2026-08-18" if date_only else ""))

    with ui.card().classes("w-full"):
        ui.label("받는 사람: pm_ta@pointmobile.com").classes(
            "text-caption text-grey-7")
        subj_in = ui.input("제목", value=draft["subject"]).props(
            "outlined dense").classes("w-full")
        body_in = ui.textarea("본문", value=draft["body"]).props(
            "outlined").classes("w-full").style("min-height:220px")

        with ui.row().classes("items-center gap-2 q-mt-sm"):
            def _send() -> None:
                ui.notify("회신을 보냈습니다. 원본 스레드에 회신으로 붙고 원문이 "
                          "인용됩니다.", color="positive", multi_line=True)
                if on_sent:
                    on_sent()

            ui.button("회신 보내기", icon="send", on_click=_send) \
                .props("unelevated color=primary")


#: 모델별 (Android 버전, API 레벨) — 확인 화면의 osType·api 를 만들 때 쓴다.
_WL_MODEL_OS = {"PM95": ("15", "35"), "PM84P": ("15", "35"), "PM86": ("11", "30"),
                "PM45": ("9", "28"), "PM90": ("9", "28")}


def _wl_verify_rows(m: dict) -> dict:
    """② 확인 결과 — **그 메일의 SN 그대로** 만든다.

    예전에는 고정 픽스처(_VERIFY)라 ① 처리에서 안내한 SN 과 ② 확인, ③ 회신의 SN 이
    서로 달랐다(사용자 지적 2026-08-28). 세 자리가 같은 목록을 봐야 한다.
    마지막 한 대만 값 불일치로 둔다 — 검증 단계가 왜 있는지 보여 주는 자리다.
    """
    sns = [s for s, _d in m.get("sns") or []]
    rows = []
    for i, sn in enumerate(sns):
        model = sn.split("-")[0]
        andr, api = _WL_MODEL_OS.get(model, (str(m.get("to_android") or "11"), "30"))
        last = (i == len(sns) - 1) and len(sns) > 1
        rows.append({
            "sn": sn, "date": "2027-05-02" if last else "2027-08-18",
            "osType": model + "_A" + andr, "androidApi": api, "ok": not last,
            "diffs": (["supportEndDate 기대 2027-08-18 · 실제 2027-05-02"]
                      if last else []),
        })
    bad = sum(1 for r in rows if not r["ok"])
    return {"total": len(rows), "matched": len(rows) - bad, "missing": 0,
            "mismatched": bad, "ok": bad == 0, "rows": rows}


def _wl_verify(S: dict, m: dict) -> None:
    v = _wl_verify_rows(m)
    ui.label(
        "일치 " + str(v["matched"]) + "/" + str(v["total"])
        + (" · 미등록 " + str(v["missing"]) if v["missing"] else "")
        + (" · 값 불일치 " + str(v["mismatched"]) if v["mismatched"] else "")
    ).classes("text-body2").style(
        "font-weight:700; color:" + ("#1b5e20" if v["ok"] else "#b3261e"))

    grid = ("display:grid; align-items:center; column-gap:.5rem; "
            "grid-template-columns:minmax(0,1fr) 8rem 7rem 4rem 1.5rem;")
    ell = "overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0;"
    with ui.element("div").classes("w-full text-caption text-grey-7").style(
            grid + "padding:.25rem 0 0;"):
        for h in ("sn", "supportEndDate", "osType", "api", ""):
            ui.label(h)
    for r in v["rows"]:
        with ui.element("div").classes("w-full").style(
                grid + "border-top:1px solid #e0e0e0; padding:.15rem 0;"):
            ui.label(r["sn"]).classes("text-caption").style(
                ell + "font-family:ui-monospace,monospace")
            ui.label(r["date"] or "-").classes("text-caption").style(ell)
            ui.label(r["osType"] or "-").classes("text-caption").style(ell)
            ui.label(r["androidApi"] or "-").classes("text-caption")
            ui.icon("check_circle" if r["ok"] else "error").props("size=xs") \
                .classes("text-green-8" if r["ok"] else "text-red-10")
        if r["diffs"]:
            ui.label(" · ".join(r["diffs"])).classes("text-caption text-red-10") \
                .style("padding-left:.5rem")
    if not v["ok"]:
        ui.label("일치하지 않는 건이 있습니다 — 다시 보내거나 FOTA 를 직접 확인하세요.") \
            .classes("text-caption text-red-10 q-mt-sm")


# ══════════════════════════════════════════════════════════════════════════
# License
# ══════════════════════════════════════════════════════════════════════════
def _lic_apply(S: dict) -> None:
    _explain(
        "라이선스 발급 신청을 정해진 양식으로 받아 접수 이력을 남깁니다. 메일과 구두로 오가던 신청을 신청 번호 단위로 관리해 진행 상황을 추적할 "
        "수 있게 합니다.")
    with ui.card().classes("w-full"):
        ui.label("신청 정보").classes("text-caption text-weight-bold")
        with ui.row().classes("w-full gap-3"):
            ui.input("Part Number", value="PMDM-STD-100").props("dense outlined").classes("flex-grow")
            ui.select(["Standard", "Premium", "Trial"], value="Standard", label="Type") \
                .props("dense outlined").style("width:150px")
            ui.input("수량", value="100").props("dense outlined").style("width:110px")
        ui.label("고객 정보").classes("text-caption text-weight-bold q-mt-sm")
        # 시연에서는 타이핑하지 않는다 — 실제 신청 건과 같은 모양으로 채워 둔다.
        with ui.row().classes("w-full gap-3"):
            ui.input("회사명", value="Helvetia Field Services AG") \
                .props("dense outlined").classes("flex-grow")
            ui.input("담당자", value="Andreas Meier") \
                .props("dense outlined").style("width:190px")
        with ui.row().classes("w-full gap-3 q-mt-sm"):
            ui.input("담당자 이메일", value="a.meier@example.ch") \
                .props("dense outlined").classes("flex-grow")
            ui.input("License User ID", value="helvetia-admin") \
                .props("dense outlined").style("width:230px")
        with ui.row().classes("w-full gap-3 q-mt-sm"):
            ui.input("복구용 이메일", value="it@example.ch") \
                .props("dense outlined").classes("flex-grow")
            ui.input("인증서 수신 이메일", value="a.meier@example.ch") \
                .props("dense outlined").classes("flex-grow")
        ui.textarea("비고", value="신규 도입 · 현장 단말 100대분") \
            .props("dense outlined").classes("w-full q-mt-sm")
        with ui.row().classes("items-center gap-2 q-mt-sm"):
            ui.icon("attach_file").classes("text-grey-7")
            ui.label("PO 파일 (최대 20MB)").classes("text-caption text-grey-7")
            ui.label("PO_2026_0821.pdf").classes("text-caption").style(
                "font-family:ui-monospace,monospace; color:#3C6553")
            ui.button("파일 선택", on_click=lambda: ui.notify(
                "파일 선택 창이 열립니다", color="primary")) \
                .props("outline size=sm")

        def _submit() -> None:
            S["licenses"].insert(0, {
                "doc_id": "PMDM-2026-0143", "date": "2026-08-19", "status": "대기중",
                "part_number": "PMDM-STD-100", "type": "Standard", "quantity": "100",
                "company": "Helvetia Field Services AG", "contact": "Andreas Meier",
                "email": "a.meier@example.ch", "user_id": "helvetia-admin",
                "recovery": "it@example.ch", "recipient": "a.meier@example.ch",
                "by": "TA", "key": "",
                "note": "신규 도입 · 현장 단말 100대분",
                "po": "PO_2026_0821.pdf", "confirm": ""})
            ui.notify("신청 완료 · 문서번호 PMDM-2026-0143 이 매겨졌습니다. "
                      "'관리' 탭 목록 맨 위에서 확인하실 수 있습니다 (시연 · 저장 없음)",
                      color="positive", multi_line=True)

        with ui.row().classes("justify-end w-full q-mt-md"):
            ui.button("신청", on_click=_submit).props("color=primary")


# ── 라이선스 인증서 ───────────────────────────────────────────────────────
def _demo_license_key() -> str:
    """시연용 라이선스 키 — 실제와 같은 모양(RSA-2048 서명의 Base64, 344자)이다.

    실제 키는 'PMDM ID-수량-발급시각' 을 사내 개인키로 서명해 Base64 로 만든다
    (license.licensing). 시연에서는 서명하지 않고 같은 길이의 문자열만 만든다 —
    형태만 진짜와 같고 어디에서도 검증되지 않는다.
    """
    import base64
    import hashlib

    blob = b""
    seed = b"tw-automation demo certificate"
    while len(blob) < 256:
        seed = hashlib.sha256(seed).digest()
        blob += seed
    return base64.b64encode(blob[:256]).decode()


def _lic_cert_html(r: dict) -> str:
    """발급되는 인증서 PDF 의 지면 — 실제 Word 템플릿 구성 그대로.

    제목·항목 순서·라이선스 키 상자·하단 문구가 실제
    work/licenses/license_cert_template.docx 와 같다. 실제로는 이 문서가 PDF 로
    변환돼 '{신청번호}_License_Certificate.pdf' 로 고객에게 첨부된다.
    """
    _MON = ["January", "February", "March", "April", "May", "June", "July",
            "August", "September", "October", "November", "December"]
    _y, _m, _d = (r["date"].split("-") + ["", "", ""])[:3]
    issued = _MON[int(_m) - 1] + " " + str(int(_d)) + ", " + _y
    qty = "{:,} EA".format(int(r["quantity"]))
    key = r["key"] or "(License Key Pending)"

    def _row(lab, val):
        return ("<tr><td class='lab'>" + lab + "</td>"
                "<td class='val'>" + val + "</td></tr>")

    return (
        "<html><head><meta charset='utf-8'><style>"
        "body{margin:0;background:#eceff1;font-family:Arial,'Malgun Gothic',"
        "sans-serif;font-size:13px;color:#1a1a1a}"
        ".page{background:#fff;margin:14px auto;padding:34px 46px 26px;"
        "max-width:660px;box-shadow:0 1px 4px rgba(0,0,0,.18)}"
        ".brand{border-bottom:2px solid #0b4da2;padding-bottom:8px;"
        "color:#0b4da2;font-size:15px;font-weight:bold;letter-spacing:.4px}"
        "h1{font-size:20px;text-align:center;margin:26px 0 24px;"
        "letter-spacing:.3px}"
        "h2{font-size:13px;margin:22px 0 6px;color:#0b4da2}"
        "table{border-collapse:collapse}"
        "td{padding:3px 0;vertical-align:top}"
        ".lab{width:150px;color:#444}"
        ".val{font-weight:bold}"
        ".keybox{border:1px solid #333;padding:10px 12px;margin:6px 0 4px;"
        "font-family:Arial,sans-serif;font-size:11px;line-height:1.9;"
        "word-break:break-all;min-height:86px}"
        ".stmt{margin:22px 0 0;line-height:1.7}"
        ".foot{border-top:1px solid #cfd8dc;margin-top:30px;padding-top:8px;"
        "text-align:center;color:#607d8b;font-size:11px}"
        "</style></head><body><div class='page'>"
        "<div class='brand'>POINT MOBILE</div>"
        "<h1>PMDM License Purchase Certificate</h1>"
        "<table>"
        + _row("Date Issued:", issued)
        + _row("Description:", "PMDM Cloud Enterprise")
        + _row("Quantity:", qty)
        + "</table>"
        "<h2>License Key</h2>"
        "<div class='keybox'>" + key + "</div>"
        "<h2>Licensee Information</h2>"
        "<table>"
        + _row("Company Name:", r["company"])
        + _row("Contact Name:", r["contact"])
        + _row("Email:", r["email"])
        + "</table>"
        "<p class='stmt'>This document certifies that the licensee named above "
        "is granted the right to use Point Mobile Device Management (PMDM) "
        "software.</p>"
        "<div class='foot'>This document confirms the licensee named above is "
        "licensed by POINT MOBILE Co., Ltd</div>"
        "</div></body></html>")


def _lic_admin(S: dict) -> None:
    _explain(
        "신청 건을 검토해 승인하면 라이선스와 인증서 발급, 고객 전달까지 이어집니다. 발급 이력이 남아 누가 언제 무엇을 승인했는지 확인할 수 "
        "있습니다.")
    COLOR = {"대기중": "orange-8", "승인": "green-8", "반려": "red-7"}

    @ui.refreshable
    def rows() -> None:
        n_wait = sum(1 for x in S["licenses"] if x["status"] == "대기중")
        with ui.row().classes("items-center gap-2 q-mb-sm"):
            _badge("대기 " + str(n_wait), "orange-8")
            _badge("전체 " + str(len(S["licenses"])), "grey-7")
        for x in S["licenses"]:
            # 승인/반려하면 목록을 다시 그린다 — 펼쳐 둔 건이 그때 접히면 방금
            # 만들어진 키와 인증서를 다시 찾아 눌러야 한다. 펼침 상태를 건에
            # 담아 두고 그대로 되살린다.
            with ui.card().classes("w-full q-mb-xs"), ui.expansion(
                    value=x.get("open", True)).classes("w-full") as exp:
                exp.on_value_change(lambda e, r=x: r.__setitem__("open", e.value))
                with exp.add_slot("header"), ui.row().classes("items-center gap-2 w-full no-wrap"):
                    ui.badge(x["status"], color=COLOR[x["status"]])
                    ui.label(x["doc_id"]).classes("text-caption").style(
                        "font-family:ui-monospace,monospace")
                    ui.label(x["company"]).classes("text-weight-medium flex-1 min-w-0 ellipsis")
                    ui.badge(x["type"], color="blue-grey-5").props("outline")
                    ui.label(x["quantity"] + "개").classes("text-caption text-grey-7")
                    ui.label(x["date"]).classes("text-caption text-grey-6")
                for lab, val in [("Part Number", x["part_number"]), ("담당자", x["contact"]),
                                 ("담당자 이메일", x["email"]), ("License User ID", x["user_id"]),
                                 ("복구용 이메일", x["recovery"]),
                                 ("인증서 수신", x["recipient"]),
                                 ("PO 파일", x["po"]), ("신청자", x["by"]),
                                 ("처리", x["confirm"] or "-"), ("비고", x["note"] or "-")]:
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        ui.label(lab).classes("text-caption text-grey-7").style("min-width:130px")
                        ui.label(val).classes("text-caption")
                if x["key"]:
                    ui.label("라이선스 키").classes("text-caption text-grey-7 q-mt-xs")
                    _mono(x["key"])
                    # 키를 만들면 고객에게 갈 인증서가 곧바로 생긴다 — 보내기 전에
                    # 회사명·수량·키가 제대로 박혔는지 이 지면에서 확인한다.
                    ui.label("인증서 미리보기 · " + x["doc_id"]
                             + "_License_Certificate.pdf") \
                        .classes("text-caption text-grey-7 q-mt-sm")
                    _srcdoc(_lic_cert_html(x), "46vh")
                if x["status"] == "대기중":
                    with ui.row().classes("gap-2 q-mt-sm"):
                        ui.button("승인 · 키 발급", on_click=lambda r=x: _approve(r)) \
                            .props("color=primary size=sm")
                        ui.button("반려", on_click=lambda r=x: _reject(r)) \
                            .props("outline color=red-7 size=sm")
                elif x["status"] == "승인":
                    with ui.row().classes("gap-2 q-mt-sm"):
                        ui.button("인증서 내려받기",
                                  on_click=lambda: ui.notify("시연 화면이라 파일이 없습니다",
                                                             color="primary")) \
                            .props("outline size=sm")
                        ui.button("메일 재발송",
                                  on_click=lambda: ui.notify("발송하지 않았습니다 (시연)",
                                                             color="primary")).props("flat size=sm")

    def _approve(r: dict) -> None:
        r["status"] = "승인"
        r["confirm"] = "2026-08-19 09:40 · demo"
        r["key"] = _demo_license_key()
        rows.refresh()
        ui.notify("승인 · 라이선스 키를 만들고 인증서를 발급했습니다. 아래 미리보기에서 "
                  "확인한 뒤 " + r["recipient"] + " 로 나갑니다 (시연 · 발송 없음)",
                  color="positive", multi_line=True)

    def _reject(r: dict) -> None:
        r["status"] = "반려"
        r["confirm"] = "2026-08-19 09:40 · demo"
        rows.refresh()
        ui.notify("반려했습니다 — 신청자에게 사유가 전달됩니다 (시연)", color="warning")

    rows()


# ══════════════════════════════════════════════════════════════════════════
# Solution Buffer
# ══════════════════════════════════════════════════════════════════════════
def _sb_issues(S: dict) -> None:
    """이슈 목록 — 실제 화면(pages/issues.render_issue_list)과 같은 구성이다.

    검색칸 → 상태 칩 → '전체 N건 중 M건 표시' → 접히는 이슈 카드 → 더 보기.
    카드를 펼치면 지표 다섯 개와 이력(타임라인)이 나온다. 이력의 아이콘·배지·
    본문 서식도 실제와 같다. 버튼과 검색은 동작하지 않는다.
    """
    ui.add_css(_RM_NOTE_CSS)
    _explain(
        "이슈 처리 속도를 측정하여 업무 기본 원칙에 준수하고 있는지를 판단할 수 있는 "
        "자료를 생성합니다.")

    ui.input(placeholder="제목·담당자·작성자·이슈번호 검색") \
        .props("dense outlined clearable debounce=300").classes("w-full q-mt-sm")

    with ui.row().classes("w-full items-center gap-1 wrap q-mt-xs"):
        for name, n in _ISSUE_STATUS_COUNTS:
            ui.chip(name + " " + format(n, ","), color=_ISSUE_COLOR.get(name, "grey-6")) \
                .props("dense text-color=white clickable")
        ui.chip("전체", color="grey-7").props("dense text-color=white clickable")

    ui.label("1,612건 중 " + str(len(S["issues"])) + "건 표시") \
        .classes("text-caption text-grey-7 q-mt-sm")

    with ui.column().classes("w-full gap-0"):
        for it in S["issues"]:
            with ui.card().classes("w-full q-mb-xs q-pa-none"), \
                    ui.expansion().props("dense dense-toggle").classes("w-full") as exp:
                with exp.add_slot("header"), \
                        ui.row().classes("items-center gap-2 w-full no-wrap"):
                    ui.badge(it["status"],
                             color=_ISSUE_COLOR.get(it["status"], "grey-6"))
                    ui.link("#" + str(it["id"]), _REDMINE + str(it["id"]),
                            new_tab=True).classes("text-caption text-primary") \
                        .tooltip("Redmine 에서 열기")
                    ui.label(it["subject"]).classes(
                        "text-weight-medium flex-1 min-w-0 ellipsis")
                    ui.badge(it["assignee"], color="blue-grey-4").props("outline")
                    ui.label(it["ago"]).classes("text-caption text-grey-6")

                with ui.column().classes("w-full gap-0 q-px-md q-pb-md"):
                    with ui.row().classes("items-center gap-4 wrap q-mt-sm"):
                        for lab, val, tip in (
                            ("첫 반응", it["first"],
                             "작성자 외 사람이 처음 손댄 시점까지 (업무일)"),
                            ("Lead time", it["resolve"],
                             "생성 → Resolved 최초 시점, 주말·한국 공휴일을 뺀 업무일"),
                            ("재오픈", str(it["reopen"]) + "회",
                             "Resolved/Closed 이후 다시 열린 횟수"),
                            ("참여자", str(it["joined"]) + "명", ""),
                            ("코멘트", str(it["comments"]) + "건", ""),
                        ):
                            with ui.column().classes("gap-0"):
                                ui.label(lab).classes("text-caption text-grey-6")
                                _lb = ui.label(val).classes(
                                    "text-body2 text-weight-medium")
                                if tip:
                                    _lb.tooltip(tip)
                    ui.separator().classes("q-my-sm")
                    for ev in it["events"]:
                        _issue_event(ev)

    ui.button("더 보기 (남은 1,604건)").props("flat no-caps color=primary") \
        .classes("w-full")


def _issue_event(ev: dict) -> None:
    """이력 한 줄 — 실제 화면(issues._render_event)과 같다."""
    icon, color = _EVENT_ICON.get(ev["kind"], ("circle", "grey-6"))
    with ui.row().classes("w-full no-wrap items-start gap-2 q-py-xs"):
        ui.icon(icon).classes("text-" + color + " q-mt-xs").style("font-size:16px")
        with ui.column().classes("gap-0 flex-1 min-w-0"):
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.label(ev["at"]).classes("text-caption text-grey-6")
                ui.label(ev["who"]).classes("text-caption text-weight-medium")
                if ev["kind"] == "created":
                    ui.badge("이슈 생성", color="primary").props("outline")
                elif ev.get("status"):
                    ui.badge(ev["status"],
                             color=_ISSUE_COLOR.get(ev["status"], "grey-6"))
            if ev.get("summary"):
                ui.label(ev["summary"]).classes("text-caption text-grey-8")
            if ev.get("note"):
                ui.html(ev["note"]).classes("rm-note text-body2") \
                    .style("max-height:26rem; overflow-y:auto")


def _sb_docs(S: dict) -> None:
    """SB 문서 — 실제 화면(pages/sb_docs.render_sb_docs)과 같은 구성이다.

    검색칸·내 담당만 → 상태 칩과 누적 사용액 → 건수 → 접히는 문서 카드.
    카드를 펼치면 상태에 따라 초안 생성 안내 / 편집기 / 업로드 결과가 나온다.
    편집기의 칸·버튼 배치도 실제와 같다. 저장·발송·업로드는 동작하지 않는다.
    """
    _explain(
        "해결된 문의를 다시 찾아 쓸 수 있는 문서로 남겨, 같은 문의에 같은 시간을 다시 "
        "쓰지 않게 합니다. 문서가 쌓일수록 대응 시간이 짧아집니다.",
        [
            "문의 내용 요약 — 고객과 주고받은 내용 전체를 읽어 증상·원인·조치·적용 "
            "버전으로 정리합니다.",
            "공개 범위 판정 — 고객사·파트너·내부 중 어디까지 공개할 수 있는 내용인지 "
            "가려냅니다.",
            "문서 초안 작성 — 정해진 양식에 맞춘 초안을 만들어, 담당자는 검토하고 "
            "올리기만 하면 됩니다.",
        ])

    with ui.row().classes("w-full items-center no-wrap gap-2 q-mt-sm"):
        ui.input(placeholder="제목·이슈번호 검색") \
            .props("dense outlined clearable debounce=300") \
            .classes("flex-1 min-w-0")
        ui.checkbox("내 담당만").props("dense").tooltip("담당자가 'TA' 인 문서만 보기")

    @ui.refreshable
    def body() -> None:
        counts: dict = {}
        for x in S["issues"]:
            if x["sb"]:
                counts[x["sb"]] = counts.get(x["sb"], 0) + 1
        with ui.row().classes("w-full items-center gap-1 wrap q-mt-xs"):
            for key, (label, color) in _SB_STATE.items():
                n = counts.get(key, 0)
                if not n or key in ("", "backlog"):
                    continue
                ui.chip(label + " " + str(n), color=color) \
                    .props("dense text-color=white clickable")
            ui.chip("전체", color="grey-8").props("dense text-color=white clickable")
            ui.space()
            ui.label("누적 사용 $63.94").classes("text-caption text-grey-6")

        rows = [x for x in S["issues"] if x["sb"]]
        ui.label("677건 중 " + str(len(rows)) + "건 표시") \
            .classes("text-caption text-grey-7 q-mt-sm")

        with ui.column().classes("w-full gap-0"):
            for x in rows:
                _sb_doc_card(x, body.refresh)

        ui.button("더 보기 (남은 673건)").props("flat no-caps color=primary") \
            .classes("w-full")

    body()


def _sb_doc_card(x: dict, refresh) -> None:
    """문서 한 건 — 실제 화면(sb_docs._render_doc)과 같다."""
    d = _SB_DOCS[x["id"]]
    with ui.card().classes("w-full q-mb-xs q-pa-none"), \
            ui.expansion().props("dense dense-toggle").classes("w-full") as exp:
        with exp.add_slot("header"), \
                ui.row().classes("items-center gap-2 w-full no-wrap"):
            _lab, _col = _SB_STATE[x["sb"]]
            ui.badge(_lab, color=_col)
            # 실제 화면은 초안이 생기기 전(backlog·queued)에는 공개 등급을 붙이지
            # 않는다 — 아직 판정하지 않은 등급을 보여 줄 수 없기 때문이다.
            if x["sb"] not in ("backlog", "queued"):
                ui.badge(d["grade"],
                         color=_SB_GRADE[d["grade"]][1]).props("outline")
            if d["revision"] > 1:
                ui.badge(str(d["revision"]) + "판", color="orange-7") \
                    .props("outline") \
                    .tooltip("이슈가 다시 열렸다가 해결되어 새로 작성된 문서")
            ui.link("#" + str(x["id"]), _REDMINE + str(x["id"]), new_tab=True) \
                .classes("text-caption text-primary")
            ui.label(d["title"] if x["sb"] != "queued"
                     else "(초안 미생성) " + x["subject"]) \
                .classes("text-weight-medium flex-1 min-w-0 ellipsis")
            ui.badge(x["assignee"], color="blue-grey-4").props("outline")

        with ui.column().classes("w-full gap-2 q-px-md q-pb-md"):
            ui.label("이슈 #" + str(x["id"]) + " [" + x["status"] + "] "
                     + x["subject"]).classes("text-caption text-grey-7")
            ui.label(d["stamps"]).classes("text-caption text-grey-6")
            if x["sb"] == "queued":
                _sb_ungenerated(x, refresh)
            elif x["sb"] == "uploaded":
                _sb_uploaded(d)
            else:
                _sb_editor(x, d, refresh)


def _sb_ungenerated(x: dict, refresh) -> None:
    with ui.row().classes("items-center gap-2"):
        ui.label("초안이 아직 생성되지 않았습니다. 5분 주기 작업이 곧 만들며, "
                 "지금 만들려면 아래를 누르세요.").classes("text-caption text-grey-7")

        def _gen() -> None:
            x["sb"] = "draft"
            refresh()
            ui.notify("초안 생성 완료 — " + _SB_DOCS[x["id"]]["title"][:50],
                      color="positive")

        ui.button("초안 생성", icon="auto_awesome", on_click=_gen) \
            .props("no-caps color=primary dense")


def _sb_uploaded(d: dict) -> None:
    with ui.row().classes("items-center gap-2"):
        ui.icon("check_circle").classes("text-green-7")
        ui.link("Confluence 에서 열기", d["url"], new_tab=True).classes("text-primary")
        for lb in d["labels"]:
            ui.badge(lb, color="blue-grey-5").props("outline")
    ui.label("Applies to: " + d["applies_to"]).classes("text-caption text-grey-7")


def _sb_editor(x: dict, d: dict, refresh) -> None:
    """draft/notified — 사람이 고치고 결정하는 영역. 실제와 칸·버튼이 같다."""
    ui.label("Claude 판단 근거: " + d["reason"]).classes("text-caption text-grey-7")
    ui.input("제목", value=d["title"]).props("dense outlined").classes("w-full") \
        .tooltip("Troubleshooting 문서는 제목을 '[TR] ' 로 시작하세요")

    with ui.row().classes("w-full items-center gap-2 no-wrap"):
        ui.select({k: k + " — " + v[0] for k, v in _SB_GRADE.items()},
                  value=d["grade"], label="공개 등급") \
            .props("dense outlined").classes("min-w-[16rem]")

    ui.input("Applies to", value=d["applies_to"]).props("dense outlined") \
        .classes("w-full")
    ui.select(["sb", "confidential", "partner", "ascp", "posted"], multiple=True,
              label="라벨", value=d["labels"]) \
        .props("dense outlined use-chips").classes("w-full") \
        .tooltip("공개 등급을 바꾸면 그 등급의 기본 라벨로 자동 교체됩니다. "
                 "'posted' 는 외부에 실제 게시한 문서에만 쓰는 라벨입니다.")

    ui.label("Description").classes("text-caption text-grey-7 q-mt-sm")
    editor = ui.editor(value=d["body"],
                       placeholder="배경 → 증상 → 원인 → 해결 순서로 작성") \
        .classes("w-full").style("min-height:22rem")
    editor._props["toolbar"] = [
        ["bold", "italic", "underline", "strike"],
        ["h2", "h3"],
        ["unordered", "ordered"],
        ["link"],
        ["removeFormat"],
        ["undo", "redo"],
        ["viewsource"],
    ]
    ui.label("서식은 위 버튼으로 넣으세요. 맨 오른쪽 </> 을 누르면 HTML 원문을 "
             "직접 고칠 수 있습니다. XHTML 문법은 안 맞아도 됩니다 — "
             "업로드할 때 Confluence 가 정규화합니다.") \
        .classes("text-caption text-grey-6")

    def _notify() -> None:
        x["sb"] = "notified"
        refresh()
        ui.notify("검토 요청 메일을 보냈습니다 → ta@pointmobile.com "
                  "(시연 · 발송 없음)", color="positive", multi_line=True)

    def _upload() -> None:
        x["sb"] = "uploaded"
        refresh()
        ui.notify("업로드 완료 (신규) — Confluence SB 스페이스 "
                  "(시연 · 실제 업로드 없음)", color="positive", multi_line=True)

    def _confirm_upload() -> None:
        with ui.dialog() as dlg, ui.card():
            ui.label("Confluence 에 업로드할까요?").classes("text-subtitle1")
            ui.label("스페이스 SB · 제목 '" + d["title"][:70] + "'") \
                .classes("text-caption text-grey-7")
            ui.label("공개 등급 " + d["grade"] + " — 되돌리려면 Confluence 에서 "
                     "직접 지워야 합니다.").classes("text-caption text-warning")
            with ui.row().classes("justify-end w-full"):
                ui.button("취소", on_click=dlg.close).props("flat no-caps")
                ui.button("업로드", on_click=lambda: (dlg.close(), _upload())) \
                    .props("color=primary no-caps")
        dlg.open()

    with ui.row().classes("items-center gap-2 q-mt-sm wrap"):
        ui.button("저장", icon="save",
                  on_click=lambda: ui.notify("저장했습니다. (시연)", color="positive")) \
            .props("no-caps dense flat")
        ui.button("미리보기·검증", icon="preview", on_click=lambda: _sb_preview(d)) \
            .props("no-caps dense flat color=primary")
        ui.button("검토 요청 메일", icon="mail", on_click=_notify) \
            .props("no-caps dense flat color=indigo") \
            .tooltip("수신: ta@pointmobile.com")
        ui.button("업로드", icon="cloud_upload", on_click=_confirm_upload) \
            .props("no-caps dense color=primary")
        ui.space()
        ui.button("폐기", icon="delete_outline",
                  on_click=lambda: (x.__setitem__("sb", "discarded"), refresh(),
                                    ui.notify("폐기했습니다.", color="info"))) \
            .props("no-caps dense flat color=grey-7")


def _sb_preview(d: dict) -> None:
    """미리보기·검증 — 실제로는 Confluence 변환 API 가 렌더한 결과를 보여 준다."""
    with ui.dialog() as dlg, ui.card().classes("w-full") \
            .style("max-width:900px"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("check_circle").classes("text-green-7")
            ui.label("문법 정상 · 제목 '" + d["title"][:60] + "'") \
                .classes("text-caption")
            for lb in d["labels"]:
                ui.badge(lb, color="blue-grey-5").props("outline")
        ui.label("아래는 Confluence 가 실제로 렌더한 결과입니다.") \
            .classes("text-caption text-grey-6")
        with ui.card().classes("w-full q-pa-sm bg-grey-1"):
            ui.html("<div class='rm-note'>" + d["body"] + "</div>")
        with ui.row().classes("justify-end w-full"):
            ui.button("닫기", on_click=dlg.close).props("flat no-caps")
    dlg.open()


def _sb_stats(S: dict) -> None:
    """통계 — 실제 화면(pages/issue_stats)과 같은 구성이다. 값도 실제 집계다."""
    _explain(
        "이슈 처리 속도를 측정하여 업무 기본 원칙에 준수하고 있는지를 판단할 수 있는 "
        "자료를 생성합니다.")
    ui.label("모든 소요시간은 이슈 이력(journal)에서 계산하고, 주말과 한국 공휴일을 "
             "뺀 업무일로 잽니다. Redmine 의 closed_on 은 Resolved 이슈 절반 이상이 "
             "비어 있고 일부는 생성일보다 과거라 쓰지 않습니다. "
             "제목이 [Test] 로 시작하는 기능 검증용 이슈는 집계에서 뺍니다.") \
        .classes("text-caption text-grey-6")

    with ui.row().classes("gap-3 q-mt-xs").style("flex-wrap: wrap"):
        for title, value, sub, color, tip in [
            ("전체 이슈", "1,612", "미해결 196건", "#6b7f8c", ""),
            ("최근 30일", "신규 15", "해결 13건", "#c0705a", ""),
            ("Lead time (평균)", "23.3 업무일", "중앙값 5.3 · 측정 1,149건",
             "#4A7C67", "생성 → Resolved. 주말·한국 공휴일을 뺀 업무일"),
            ("첫 반응 (평균)", "10.3 업무일", "중앙값 0.6 · 측정 1,481건",
             "#3b6ea5",
             "작성자 외 사람이 처음 손댄 시점 — 코멘트뿐 아니라 상태·담당·첨부 "
             "변경도 포함"),
            ("재오픈율", "20.0%", "한 번이라도 다시 열린 비율", "#e07a2c",
             "Resolved/Closed 이후 다시 열린 이력이 있는 이슈 비율"),
        ]:
            with ui.card().classes("items-start").style(
                    "min-width:170px; border-left:4px solid " + color):
                ui.label(title).classes("text-caption text-grey-7")
                ui.label(value).classes("text-h5")
                _lb = ui.label(sub).classes("text-caption text-grey-6")
                if tip:
                    _lb.tooltip(tip)

    with ui.card().classes("w-full"):
        ui.echart({
            "title": {"text": "월별 생성 · 해결", "textStyle": {"fontSize": 13}},
            "tooltip": {"trigger": "axis"},
            "legend": {"data": ["생성", "해결"], "right": 10, "top": 4},
            "grid": {"left": 45, "right": 20, "top": 45, "bottom": 60},
            "xAxis": {"type": "category", "data": _STAT_MONTHS,
                      "axisLabel": {"rotate": 45, "fontSize": 10}},
            "yAxis": {"type": "value"},
            "series": [
                {"name": "생성", "type": "bar", "data": _STAT_CREATED,
                 "itemStyle": {"color": "#c0705a"}},
                {"name": "해결", "type": "bar", "data": _STAT_RESOLVED,
                 "itemStyle": {"color": "#4A7C67"}},
            ],
        }).classes("w-full").style("height:300px")

    with ui.card().classes("w-full"):
        ui.label("첫 반응").classes("text-subtitle2")
        ui.label("작성자 말고 처음 손댄 사람이 무엇을 했는지로 나눕니다. "
                 "코멘트만 세면 담당만 배정하고 넘어간 건이 '반응 없음'이 됩니다.") \
            .classes("text-caption text-grey-6")
        ui.echart({
            "tooltip": {"trigger": "item"},
            "legend": {"bottom": 0, "type": "scroll"},
            "series": [{
                "type": "pie", "radius": ["38%", "62%"], "center": ["50%", "44%"],
                "label": {"show": False},
                "data": [{"name": n, "value": v, "itemStyle": {"color": c}}
                         for n, v, c in _STAT_ACTIONS],
            }],
        }).classes("w-full").style("height:280px")

    with ui.row().classes("w-full gap-3 no-wrap items-stretch"):
        with ui.card().classes("flex-1 min-w-0"):
            ui.echart({
                "title": {"text": "상태 분포", "textStyle": {"fontSize": 13}},
                "tooltip": {"trigger": "item"},
                "legend": {"bottom": 0, "type": "scroll"},
                "series": [{
                    "type": "pie", "radius": ["38%", "62%"],
                    "center": ["50%", "46%"], "label": {"show": False},
                    "data": [{"name": n, "value": v, "itemStyle": {"color": c}}
                             for n, v, c in _STAT_STATUS],
                }],
            }).classes("w-full").style("height:300px")
        with ui.card().classes("flex-1 min-w-0"):
            ui.label("담당자별").classes("text-subtitle2")
            ui.table(columns=[
                {"name": "name", "label": "담당자", "field": "name",
                 "align": "left", "sortable": True},
                {"name": "total", "label": "전체", "field": "total",
                 "sortable": True},
                {"name": "open", "label": "미해결", "field": "open",
                 "sortable": True},
                {"name": "lead", "label": "Lead time 평균(업무일)", "field": "lead",
                 "sortable": True},
            ], rows=_STAT_ASSIGNEES, row_key="name") \
                .props("dense flat").classes("w-full")


# ══════════════════════════════════════════════════════════════════════════
# 뉴스레터 미리보기 — 시연 화면의 iframe 이 직접 불러가는 주소
# ══════════════════════════════════════════════════════════════════════════
#: 한 번 렌더한 HTML 을 들고 있는다. 시연 데이터는 고정이라 매번 다시 만들 이유가 없다.
_NL_HTML: dict[str, str] = {}


@ng_app.get("/demo/newsletter/{lang}")
def _demo_newsletter_html(lang: str):
    """실제 발송 템플릿으로 렌더한 뉴스레터 HTML(시연용, 파일 생성 없음).

    이 라우트는 /demo 화면의 iframe 과 '새 창으로' 링크가 가리키는 자리다. 화면 쪽만
    남고 이 함수가 지워진 적이 있어(547e791) 미리보기가 통째로 404 였다 — 짝이 되는
    코드라 함께 두고, scripts/check_demo_routes.py 가 둘이 맞는지 지킨다.
    """
    from fastapi.responses import HTMLResponse

    lang = "en" if lang == "en" else "kr"
    if lang not in _NL_HTML:
        from app.office import newsletter as NL

        cards = [{"topic": c["topic"],
                  "title": c["title_en" if lang == "en" else "title_ko"],
                  "body": c["body_en" if lang == "en" else "body_ko"]} for c in _NL_CARDS]
        _NL_HTML[lang] = NL.render(
            lang, product="PM95 (Android 15)", product_short="PM95",
            os_version="95.52 (Jul 20 2026)", sdk="3.41.09",
            rn_link="https://www.dropbox.com/scl/fo/demo/PM95_A15_Release_Note_95.52.pdf",
            cards=cards, android="15", security_patch="July 5, 2026")
    return HTMLResponse(_NL_HTML[lang])


# ══════════════════════════════════════════════════════════════════════════
# 페이지
# ══════════════════════════════════════════════════════════════════════════
#: 모듈 → 하위 화면. 이름과 아이콘은 **실제 화면 그대로**다
#: (whitelist.py · license.py · issues.py 의 ui.tab 참고).
_MODULES = [
    ("osupdate", "OS Update", [("작업", "assignment", _os_pipeline)]),
    ("whitelist", "WhiteList", [("메일함", "mail", _wl_mailbox)]),
    ("license", "License", [
        ("신청", "add_box", _lic_apply), ("관리자", "admin_panel_settings", _lic_admin)]),
    # 이슈가 먼저다 — Redmine 이슈를 보고 그중 해결된 건이 SB 문서가 되는 순서라
    # 화면도 그 흐름대로 놓는다. 첫 탭이 이 모듈을 열었을 때 보이는 화면이 된다.
    ("sb", "Solution Buffer", [
        ("이슈", "bug_report", _sb_issues),
        ("Solution Buffer", "description", _sb_docs), ("통계", "insights", _sb_stats)]),
]


@ui.page("/demo")
def demo_page() -> None:
    apply_theme()
    S = _fresh()
    with page_header("시연", show_nav=False, max_w="max-w-7xl"):
        with ui.tabs().classes("w-full") as mods:
            for key, label, _subs in _MODULES:
                ui.tab(key, label=label)
        with ui.tab_panels(mods, value=_MODULES[0][0]).classes("w-full"):
            for key, _label, subs in _MODULES:
                with ui.tab_panel(key):
                    if len(subs) == 1:
                        subs[0][2](S)
                        continue
                    with ui.tabs().props("dense align=left no-caps").classes(
                            "w-full") as sub:
                        for name, icon, _fn in subs:
                            ui.tab(key + "-" + name, label=name, icon=icon)
                    with ui.tab_panels(sub, value=key + "-" + subs[0][0]) \
                            .classes("w-full"):
                        for name, _icon, fn in subs:
                            with ui.tab_panel(key + "-" + name):
                                fn(S)
