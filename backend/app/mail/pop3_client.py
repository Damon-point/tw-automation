"""POP3 클라이언트: Outlook 등 POP3 메일 서버에서 메일을 읽어온다.

- 절대 DELE(삭제) 하지 않는다. 읽기 전용.
- UIDL(서버 고유 ID)로 이미 받은 메일을 구분해 신규만 가져오도록 지원.
"""
from __future__ import annotations

import email
import poplib
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr
from typing import Callable

# poplib 기본 한 줄 한도(2048B)는 base64 첨부·긴 헤더가 든 실메일에서 쉽게 넘어
# error_proto('line too long') 를 낸다. 실메일은 76자 줄바꿈이 보통이지만 이를 안 지키는
# 발신 클라이언트가 있어 넉넉히 올린다(1MB). 읽기 전용이라 부작용 없음.
poplib._MAXLINE = 1024 * 1024


@dataclass
class ParsedMail:
    uidl: str
    sender: str = ""
    sender_addr: str = ""
    subject: str = ""
    received_at: str = ""
    body_text: str = ""
    body_html: str = ""
    message_id: str = ""  # 메일 헤더 Message-ID (팀 공용 중복제거 키)
    # 스레드(대화) 연결용 헤더. 제목만으로 묶으면 '같은 제목의 새 요청'이 한 덩어리가 된다.
    in_reply_to: str = ""
    references: str = ""
    # 원본 수신자(전체 회신 시 참조인 보존).
    to_recipients: str = ""
    cc_recipients: str = ""
    # 앱이 보낸 메일 표식(X-TW-Automation) — 우리 회신을 신규 요청으로 오인하지 않게.
    app_marker: str = ""
    # 첨부(엑셀/CSV) — 파일명, 추출 SN(줄바꿈 구분), 저장 폴더.
    attachment_names: str = ""
    attachment_serials: str = ""
    attachment_dir: str = ""


@dataclass
class ConnectionConfig:
    email_address: str
    password: str
    server: str = "outlook.office365.com"
    port: int = 995
    use_ssl: bool = True


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _connect(cfg: ConnectionConfig) -> poplib.POP3:
    if cfg.use_ssl:
        client: poplib.POP3 = poplib.POP3_SSL(cfg.server, cfg.port, timeout=30)
    else:
        client = poplib.POP3(cfg.server, cfg.port, timeout=30)
    client.user(cfg.email_address)
    client.pass_(cfg.password)
    return client


def test_connection(cfg: ConnectionConfig) -> tuple[bool, str]:
    """접속 테스트. (성공여부, 메시지) 반환."""
    try:
        client = _connect(cfg)
        try:
            count, _size = client.stat()
        finally:
            client.quit()
        return True, f"접속 성공 · 메일함에 {count}통 있습니다."
    except poplib.error_proto as e:
        return False, f"로그인 실패: {e}"
    except Exception as e:  # noqa: BLE001
        return False, f"접속 오류: {e}"


def _server_uidls(client: poplib.POP3) -> list[tuple[int, str]]:
    """(메시지번호, uidl) 목록. 최신이 뒤에 오도록 서버 순서 유지."""
    resp, lines, _ = client.uidl()
    result: list[tuple[int, str]] = []
    for line in lines:
        parts = line.decode("ascii", errors="replace").split()
        if len(parts) >= 2:
            result.append((int(parts[0]), parts[1]))
    return result


def _extract_bodies(msg: Message) -> tuple[str, str]:
    """메시지에서 (plain text, html) 본문을 추출."""
    text, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            if ctype == "text/plain" and not text:
                text = _payload_to_str(part)
            elif ctype == "text/html" and not html:
                html = _payload_to_str(part)
    else:
        ctype = msg.get_content_type()
        body = _payload_to_str(msg)
        if ctype == "text/html":
            html = body
        else:
            text = body
    return text, html


def _payload_to_str(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _header_subject_and_from(msg: Message) -> tuple[str, str]:
    """메시지(헤더 포함)에서 (디코딩된 제목, 발신자 이메일주소)."""
    subject = _decode(msg.get("Subject", ""))
    _name, from_addr = parseaddr(msg.get("From", ""))
    return subject, from_addr


def _parse_message(uidl: str, raw: bytes) -> ParsedMail:
    msg = email.message_from_bytes(raw)
    from_name, from_addr = parseaddr(msg.get("From", ""))
    display_name = _decode(from_name)
    sender = f"{display_name} <{from_addr}>".strip() if display_name else from_addr
    text, html = _extract_bodies(msg)
    msgid = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip()
    # 첨부(엑셀/CSV) — 파일 보관 + SN 추출. 실패해도 메일 저장은 진행.
    att = {"names": [], "serials": [], "dir": ""}
    try:
        from app.mail import attachments as _ATT
        att = _ATT.process_message(msg, msgid or uidl, raw)
    except Exception:  # noqa: BLE001
        pass
    return ParsedMail(
        uidl=uidl,
        sender=sender,
        sender_addr=from_addr,
        subject=_decode(msg.get("Subject", "")),
        received_at=msg.get("Date", ""),
        body_text=text,
        body_html=html,
        message_id=msgid,
        in_reply_to=(msg.get("In-Reply-To") or msg.get("In-reply-to") or "").strip(),
        references=(msg.get("References") or "").strip(),
        to_recipients=_decode(", ".join(msg.get_all("To", []))),
        cc_recipients=_decode(", ".join(msg.get_all("Cc", []))),
        attachment_names=", ".join(att.get("names") or []),
        attachment_serials="\n".join(att.get("serials") or []),
        attachment_dir=att.get("dir") or "",
        app_marker=(msg.get("X-TW-Automation") or "").strip(),
    )


def _fetch_headers(client: poplib.POP3, num: int) -> Message:
    """TOP 으로 헤더만(본문 0줄) 가져온다. 서버가 TOP 미지원이면 RETR 로 대체."""
    try:
        _resp, lines, _octets = client.top(num, 0)
    except poplib.error_proto:
        _resp, lines, _octets = client.retr(num)
    return email.message_from_bytes(b"\r\n".join(lines))


def _retr_full(client: poplib.POP3, num: int) -> bytes:
    _resp, lines, _octets = client.retr(num)
    return b"\r\n".join(lines)


# 한 번의 동기화에서 받을 메일 수 상한. 세션이 길어질수록 서버가 중간에 끊을 확률이
# 올라간다(실측: 21통째에서 EOF). 남은 것은 다음 주기에 이어 받는다 — 진척이 저장되므로
# 매번 앞으로 나아간다.
MAX_PER_SYNC = 40

# 메일 1통을 받다 끊겼을 때 세션을 다시 맺어 이어갈 최대 횟수.
_MAX_RECONNECT = 2


def _is_conn_dead(e: BaseException) -> bool:
    """연결이 죽은 오류인가 — 그렇다면 같은 세션으로는 더 진행할 수 없다."""
    import socket
    import ssl

    return isinstance(e, (ssl.SSLError, socket.error, OSError, EOFError,
                          poplib.error_proto))


def fetch_new_matching_mails(
    cfg: ConnectionConfig,
    seen_uidls: set[str],
    match_fn: Callable[[str, str], bool],
    *,
    max_mails: int = MAX_PER_SYNC,
) -> tuple[list[ParsedMail], list[str], list[str]]:
    """seen_uidls 에 없는 신규 메일을 평가한다.

    - 각 신규 메일은 먼저 헤더(TOP)만 받아 match_fn(subject, from_addr) 로 필터.
    - 조건에 맞는 메일만 본문까지(RETR) 받아 ParsedMail 로 반환.

    **중간에 끊겨도 그때까지 받은 것은 돌려준다.** 예전에는 한 통이 실패하면 예외가
    그대로 올라가 이미 받은 수십 통이 통째로 버려졌고, 다음 실행도 같은 메일에서 같은
    자리에 멈춰 동기화가 영구히 정지했다(실측: 3일간 0건). 그래서
      · 메일 1통의 실패는 그 통만 건너뛴다
      · 연결이 죽으면 재접속해 이어가고, 그마저 안 되면 **받은 만큼** 반환한다
      · 한 번에 max_mails 통까지만 받는다 — 나머지는 다음 주기에 이어 받는다

    반환값: (조건에 맞는 메일들, 저장해도 되는 UIDL 목록, 건너뛴 UIDL 목록)
    """
    client = _connect(cfg)
    matched: list[ParsedMail] = []
    processed: list[str] = []          # 성공적으로 평가한 것만 — 이것만 '받음'으로 기록한다
    failed: list[str] = []
    reconnects = 0
    try:
        pairs = _server_uidls(client)
        # 서버상 뒤쪽(대개 최신)부터 처리
        new_pairs = [(num, u) for num, u in reversed(pairs) if u not in seen_uidls]
        new_pairs = new_pairs[:max_mails]

        i = 0
        while i < len(new_pairs):
            num, uidl = new_pairs[i]
            try:
                headers = _fetch_headers(client, num)
                subject, from_addr = _header_subject_and_from(headers)
                if match_fn(subject, from_addr):
                    matched.append(_parse_message(uidl, _retr_full(client, num)))
                processed.append(uidl)
                i += 1
            except Exception as e:  # noqa: BLE001
                if not _is_conn_dead(e) or reconnects >= _MAX_RECONNECT:
                    # 이 메일만의 문제이거나 재접속을 다 썼다. 건너뛰고 다음으로.
                    failed.append(uidl)
                    i += 1
                    if _is_conn_dead(e):
                        break          # 연결이 죽었으면 더 진행할 수 없다
                    continue
                # 연결이 끊겼다 — 다시 맺고 **같은 메일부터** 이어간다.
                reconnects += 1
                try:
                    client.quit()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    client = _connect(cfg)
                except Exception:  # noqa: BLE001
                    break              # 재접속도 안 되면 받은 만큼만 돌려준다
        return matched, processed, failed
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001
            pass
