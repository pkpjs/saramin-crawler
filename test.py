name: Run Saramin Crawler

on:
  workflow_dispatch:      # Actions 탭에서 수동 실행 가능
  schedule:
    - cron: "0 0 * * *"   # 매일 한국시간 오전 9시 (UTC+0 기준 00시)

jobs:
  run:
    runs-on: ubuntu-latest
    permissions:
      contents: write    # docs 폴더에 HTML 저장 후 커밋하기 위함

    env:
      EMAIL_SENDER: ${{ secrets.EMAIL_SENDER }}
      EMAIL_RECEIVER: ${{ secrets.EMAIL_RECEIVER }}
      EMAIL_APP_PASSWORD: ${{ secrets.EMAIL_APP_PASSWORD }}

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0   # 전체 기록 가져오기 (rebase를 위해 필요)

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.10"

      - name: Install dependencies
        run: |
          pip install --upgrade pip
          pip install requests beautifulsoup4 pandas google-auth google-auth-oauthlib google-api-python-client

      - name: Run Saramin Crawler
        run: python test.py   # ⚠ 여기서 파일명 확인해서 맞게 수정하세요

      - name: Commit and Push HTML & CSV
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"

          # ✅ 원격 변경사항을 rebase 방식으로 가져오기
          git pull --rebase origin main || true

          # docs/*.html(HTML)과 *.csv(CSV)를 스테이징
          git add docs/*.html *.csv || true

          if git diff --cached --quiet; then
            echo "✅ 변경된 파일 없음. 커밋 생략."
          else
            git commit -m "🔄 Auto update saramin_results_latest.html & CSV ($(date -u +'%Y-%m-%dT%H:%M:%SZ'))"
            git push origin main
            echo "✅ 변경사항이 성공적으로 커밋 및 푸시되었습니다."
          fi
