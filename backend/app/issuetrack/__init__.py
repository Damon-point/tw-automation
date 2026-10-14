"""Redmine 이슈 트래킹 모니터.

사내 Redmine(https://issue.pointmobile.co.kr) 의 salesissue 프로젝트를 감시해
이슈와 그 안에서 일어난 **모든 이벤트**(상태·담당자·우선순위 변경, 코멘트, 첨부,
연결 이슈)를 로컬에 쌓는다. 읽기 전용 — Redmine 에는 아무 것도 쓰지 않는다.

app/integrations/redmine.py 와 역할이 다르다. 저쪽은 Playwright 로 이슈 HTML 을
읽어 OS Update 파이프라인에 쓰는 헬퍼이고, 여기는 REST(.json)로 전수/증분 수집해
목록·타임라인·통계·Solution Buffer 문서화의 원천 데이터를 만든다.
"""
