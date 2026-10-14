"""보안 유틸: 로그인 비밀번호 해시(bcrypt) + 자격증명 암복호화(Fernet).

FERNET_KEY 는 .env 에 저장한다. 없으면 자격증명 암복호화 시 에러를 낸다.
키 생성:  python -m scripts.gen_fernet_key
"""
import os

import bcrypt
from cryptography.fernet import Fernet


# --- 로그인 비밀번호 (단방향 해시) --------------------------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


# --- 자격증명 (양방향 암호화) -------------------------------------------
def _fernet() -> Fernet:
    key = os.getenv("FERNET_KEY")
    if not key:
        raise RuntimeError(
            "FERNET_KEY 가 설정되지 않았습니다. `python -m scripts.gen_fernet_key` 로 "
            "키를 생성해 .env 에 넣으세요."
        )
    return Fernet(key.encode("utf-8"))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def generate_fernet_key() -> str:
    return Fernet.generate_key().decode("utf-8")
