"""PMDM License 발급 요청/승인 모듈 (독립 기능).

기존 앱과 완전 격리되어 동작한다:
  - 신규 테이블(license_requests)만 추가 → 기존 스키마 불변
  - 관리자 판별은 env(LICENSE_ADMIN_USERS) → User 모델 변경 없음
  - docxtpl / SMTP 는 지연 import → 미설치여도 기존 앱 실행에 영향 없음
"""
