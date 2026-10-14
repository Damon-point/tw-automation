"""PMDM License Key 생성/검증 — RSA(개인키 암호화 / 공개키 복호화).

Java RSACrypto 이식. 페이로드 = "{pmdm_id}-{count}-{time_ms}".
  · 발급: 개인키로 암호화(RSA, PKCS#1 v1.5 **type-1** 패딩) + Base64 = license_key
  · 검증: 공개키로 복호화 후 파싱(뒤에서 두 번째 '-' 기준 → id / count / time)
Java `Cipher.getInstance("RSA")`(= RSA/ECB/PKCS1Padding) 와 바이트 단위로 동일.

**보안 원칙**
  · 키는 오직 .env(LICENSE_RSA_PRIVATE_KEY / LICENSE_RSA_PUBLIC_KEY)에서만 로드한다.
  · 키·평문·알고리즘 세부를 **로그/UI/외부로 절대 내보내지 않는다**.
  · 이 모듈은 **anthropic 를 import 하지 않으며**, License 모듈 전체가 결정적이라
    키/알고리즘이 **어떤 Claude 호출 경로에도 들어가지 않는다**.
"""
from __future__ import annotations

import base64
import os
import time

from cryptography.hazmat.primitives.serialization import (
    load_der_private_key,
    load_der_public_key,
)


def keys_configured() -> bool:
    return bool(os.getenv("LICENSE_RSA_PRIVATE_KEY", "").strip()
                and os.getenv("LICENSE_RSA_PUBLIC_KEY", "").strip())


def _priv():
    b64 = os.getenv("LICENSE_RSA_PRIVATE_KEY", "").strip()
    if not b64:
        raise RuntimeError("LICENSE_RSA_PRIVATE_KEY 미설정(.env 에 개인키(Base64) 필요)")
    return load_der_private_key(base64.b64decode(b64), password=None)


def _pub():
    b64 = os.getenv("LICENSE_RSA_PUBLIC_KEY", "").strip()
    if not b64:
        raise RuntimeError("LICENSE_RSA_PUBLIC_KEY 미설정(.env 에 공개키(Base64) 필요)")
    return load_der_public_key(base64.b64decode(b64))


def _kbytes(n: int) -> int:
    return (n.bit_length() + 7) // 8


def _encrypt_priv(payload: str) -> str:
    """개인키 암호화 = PKCS#1 v1.5 type-1 패딩(00 01 FF..FF 00 || msg) + raw RSA(d) + Base64."""
    priv = _priv()
    pn = priv.private_numbers()
    n, d = pn.public_numbers.n, pn.d
    k = _kbytes(n)
    msg = payload.encode("utf-8")
    pad_len = k - 3 - len(msg)
    if pad_len < 8:
        raise ValueError("payload 가 키 크기에 비해 너무 깁니다.")
    padded = b"\x00\x01" + b"\xff" * pad_len + b"\x00" + msg
    c = pow(int.from_bytes(padded, "big"), d, n)
    return base64.b64encode(c.to_bytes(k, "big")).decode()


def _decrypt_pub(license_key: str) -> str:
    """공개키 복호화 = raw RSA(e) + type-1 패딩 제거."""
    pub = _pub()
    pubn = pub.public_numbers()
    n, e = pubn.n, pubn.e
    k = _kbytes(n)
    c = int.from_bytes(base64.b64decode(license_key), "big")
    b = pow(c, e, n).to_bytes(k, "big")
    if b[0] != 0 or b[1] != 1:
        raise ValueError("패딩 헤더 불일치 — 키가 손상됐거나 이 공개키로 발급된 키가 아닙니다.")
    sep = b.index(b"\x00", 2)  # 패딩 끝 구분자
    return b[sep + 1:].decode("utf-8")


def generate_license_key(pmdm_id: str, count) -> tuple[str, dict]:
    """(license_key, info). payload = "{id}-{count}-{time_ms}". id=PMDM ID, count=단말 대수."""
    pmdm_id = (pmdm_id or "").strip()
    count = str(count).strip()
    ms = int(time.time() * 1000)
    payload = f"{pmdm_id}-{count}-{ms}"
    key = _encrypt_priv(payload)
    return key, {"id": pmdm_id, "count": count, "time_ms": ms, "payload": payload}


def verify_license_key(license_key: str) -> dict:
    """공개키로 복호화해 파싱. {ok, id, count, time_ms, payload} 또는 {ok:False, error}.

    Java 파싱 규칙과 동일: 뒤에서 두 번째 '-' 기준으로 id / count 를 나눈다
    (→ id 안에 '-' 가 있어도 안전).
    """
    try:
        payload = _decrypt_pub(license_key)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    last = payload.rfind("-")
    second = payload.rfind("-", 0, last - 1) if last > 0 else -1
    if last < 0 or second < 0:
        return {"ok": True, "id": payload, "count": "", "time_ms": "", "payload": payload}
    return {
        "ok": True,
        "id": payload[:second],
        "count": payload[second + 1:last],
        "time_ms": payload[last + 1:],
        "payload": payload,
    }
