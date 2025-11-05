# test

[채용공고 대시보드 보기](https://pkpjs.github.io/test/saramin_results_latest.html)

## 지원 완료 상태 자동 업데이트

- `update_status.py`가 Gmail 지원 완료 메일을 읽고 `docs/saramin_results_latest.html`에 `지원상태` 컬럼을 추가합니다.
- GitHub Actions 워크플로우(`Update application status`)가 매일 00시에 실행되어 상태를 갱신하고, 변경 사항이 있으면 자동으로 커밋합니다.
- 사용 전 GitHub Secrets에 `GMAIL_TOKEN_JSON`(OAuth `token.json` 내용 또는 base64 인코딩 문자열)을 등록하세요.
