import math
import time
import os
import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

class SaraminCrawler:
    def __init__(self):
        self.api_url = "https://www.saramin.co.kr/zf_user/search/get-recruit-list"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.saramin.co.kr/zf_user/search",
            "X-Requested-With": "XMLHttpRequest",
        }
        # 🔎 검색 조건(요청 조건)
        self.params = {
            "searchType": "search",
            "loc_mcd": "106000,104000,105000,107000,110000,111000",   # 부산/대구/대전/울산/경남/경북
            "cat_kewd": "83,84,85,90,104,108,111,112,114,116",       # 데이터엔지니어 외 10개
            "company_cd": "0,1,2,3,4,5,6,7,9,10",                    # 회사형태 전체
            "exp_cd": "1",                                           # 신입
            "exp_none": "y",                                         # 경력무관 포함
            "job_type": "1",                                         # 정규직
            "search_optional_item": "y",
            "search_done": "y",
            "panel_count": "y",
            "preview": "y",
            "recruitPage": 1,
            "recruitPageCount": 40,                                  # 페이지당 40개
            "recruitSort": "relation"                                # 관련도순
        }

    # ---------- 검색결과 파싱 ----------
    def _parse_jobs_from_innerHTML(self, inner_html):
        soup = BeautifulSoup(inner_html, "html.parser")
        jobs = []
        for item in soup.select("div.item_recruit"):
            try:
                rec_idx = (item.get("value") or "").strip()
                a = item.select_one("h2.job_tit a")
                if not a:
                    continue
                title = a.get_text(strip=True)
                href = a.get("href", "")
                link = "https://www.saramin.co.kr" + href if href.startswith("/") else href

                company_el = item.select_one("strong.corp_name a, strong.corp_name")
                company = company_el.get_text(strip=True) if company_el else ""

                cond_spans = item.select("div.job_condition span")
                location = cond_spans[0].get_text(strip=True) if len(cond_spans) > 0 else ""
                career   = cond_spans[1].get_text(strip=True) if len(cond_spans) > 1 else ""
                edu      = cond_spans[2].get_text(strip=True) if len(cond_spans) > 2 else ""

                deadline_el = item.select_one("div.job_date span.date")
                deadline = deadline_el.get_text(strip=True) if deadline_el else ""

                jobs.append({
                    "rec_idx": rec_idx,
                    "title": title,
                    "company": company,
                    "location": location,
                    "career": career,
                    "education": edu,
                    "deadline": deadline,
                    "link": link,
                    "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
            except Exception:
                continue
        return jobs

    def _fetch_page(self, page: int):
        params = dict(self.params)
        params["recruitPage"] = page
        resp = requests.get(self.api_url, params=params, headers=self.headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        inner_html = data.get("innerHTML", "")
        count_str = data.get("count", "0")
        try:
            total_count = int(str(count_str).replace(",", ""))
        except Exception:
            total_count = 0

        jobs = self._parse_jobs_from_innerHTML(inner_html) if inner_html else []
        return jobs, total_count

    def crawl_all(self, sleep_sec=0.6, page_limit=None) -> pd.DataFrame:
        print("🔎 수집 시작...")
        all_jobs = []
        first_page_jobs, total_count = self._fetch_page(1)
        if not first_page_jobs:
            print("⚠ 첫 페이지에서 공고를 찾지 못했습니다. (헤더/파라미터 확인 필요)")
            return pd.DataFrame()

        all_jobs.extend(first_page_jobs)

        page_count = math.ceil(total_count / int(self.params["recruitPageCount"])) if total_count else 1
        if page_limit is not None:
            page_count = min(page_count, page_limit)

        print(f"📊 총 {total_count}건 추정, {page_count}페이지 예정")

        for p in range(2, page_count + 1):
            print(f"📄 {p}/{page_count} 페이지 수집 중...")
            jobs, _ = self._fetch_page(p)
            if not jobs:
                print("⛔ 더 이상 공고가 없습니다.")
                break
            all_jobs.extend(jobs)
            time.sleep(sleep_sec)

        df = pd.DataFrame(all_jobs)
        if df.empty:
            print("⚠ 수집된 데이터가 없습니다.")
            return df

        # 중복 제거: rec_idx 우선, 없으면 link
        if "rec_idx" in df.columns:
            df["__dedup_key"] = df["rec_idx"].where(df["rec_idx"].astype(bool), other=df["link"])
            df.drop_duplicates(subset=["__dedup_key"], inplace=True)
            df.drop(columns=["__dedup_key"], inplace=True)
        else:
            df.drop_duplicates(subset=["link"], inplace=True)

        return df

    # ---------- HTML/이메일 ----------
    def build_html_page(self, df: pd.DataFrame, out_html_path: str, page_title: str = "채용공고 결과"):
        """DataFrame을 HTML 페이지로 저장 (GitHub Pages 용)"""
        out_path = Path(out_html_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        styled = (
            df[['title','company','location','career','education','deadline','link','crawled_at']]
            .rename(columns={
                'title':'제목','company':'회사','location':'위치',
                'career':'경력','education':'학력','deadline':'마감일',
                'link':'링크','crawled_at':'수집시각'
            })
            .copy()
        )

        # 링크 컬럼 HTML로 변환 (새 창)
        styled['링크'] = styled['링크'].apply(lambda x: f'<a href="{x}" target="_blank" rel="noopener">바로가기</a>' if x else '')

        table_html = styled.to_html(index=False, escape=False, justify="center", border=0)

        html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{page_title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }}
.container {{ max-width:1200px; margin:0 auto; }}
h1 {{ font-size:24px; margin-bottom:8px; }}
.desc {{ color:#666; margin-bottom:20px; }}
table {{ width:100%; border-collapse:collapse; table-layout:fixed; word-break:break-word; }}
th, td {{ border-bottom:1px solid #eee; padding:12px 8px; text-align:left; vertical-align:top; }}
th {{ background:#fafafa; font-weight:700; }}
tr:hover {{ background:#f4f9ff; }}
a {{ text-decoration:none; color:#3498db; }}
.meta {{ color:#888; font-size:13px; margin-top:20px; }}
</style>
</head>
<body>
<div class="container">
  <h1>{page_title}</h1>
  <div class="desc">생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
  {table_html}
  <div class="meta">Powered by Python · 자동 크롤링 · Updated everyday</div>
</div>
</body>
</html>"""
        out_path.write_text(html, encoding="utf-8")
        print(f"🌐 HTML 생성: {out_path}")
        return str(out_path)

    def generate_html_table_for_email(self, jobs, max_rows=10):
        """이메일 본문용 테이블 (상위 max_rows개)"""
        subset = jobs[:max_rows]
        html = "<table style='width:100%; border-collapse:collapse; margin-top:20px;'>"
        html += "<tr style='background:#667eea; color:white;'><th>제목</th><th>회사</th><th>위치</th><th>경력</th><th>학력</th><th>마감일</th></tr>"
        for job in subset:
            html += (
                f"<tr>"
                f"<td>{job.get('title','')}</td>"
                f"<td>{job.get('company','')}</td>"
                f"<td>{job.get('location','')}</td>"
                f"<td>{job.get('career','')}</td>"
                f"<td>{job.get('education','')}</td>"
                f"<td>{job.get('deadline','')}</td>"
                f"</tr>"
            )
        html += "</table>"
        return html

    def send_email(self, jobs, sender_email, app_password, receiver_email, pages_url: str):
        """CSV 첨부 없이 HTML 본문만 전송"""
        if not jobs:
            print("⚠ 전송할 공고가 없습니다.")
            return

        subject = f"🎯 채용공고 자동 수집 결과 - {datetime.now().strftime('%Y-%m-%d')}"
        html_table = self.generate_html_table_for_email(jobs, max_rows=10)

        html_body = f"""
        <html><head><meta charset="UTF-8"></head>
        <body style="font-family:'Apple SD Gothic Neo',Arial,sans-serif;">
        <h1>🎯 채용공고 자동 수집 결과</h1>
        <p>{datetime.now().strftime('%Y년 %m월 %d일')} 수집 완료</p>
        <div>
          <h2>📊 수집 현황</h2>
          <p>• <strong>총 {len(jobs)}개</strong> 공고 발견</p>
        </div>
        <div>
          <h2>🔥 주요 공고 미리보기 (최대 10개)</h2>
          {html_table}
        </div>
        <div style="text-align:center; margin:30px 0;">
          <a href="{pages_url}" style="display:inline-block; padding:12px 20px; background:#3498db; color:#fff; text-decoration:none; border-radius:6px;">🌐 전체 공고 보기</a>
        </div>
        <p style="font-size:12px; color:#888;">🤖 Python 자동화 시스템이 수집했습니다 (CSV 미첨부)</p>
        </body></html>
        """

        msg = MIMEMultipart('alternative')
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = subject
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        try:
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(sender_email, app_password)
            server.send_message(msg)
            server.quit()
            print("📧 이메일 전송 완료! (CSV 첨부 없음)")
        except Exception as e:
            print(f"❌ 이메일 전송 실패: {e}")

if __name__ == "__main__":
    crawler = SaraminCrawler()

    # 1) 크롤링
    df = crawler.crawl_all(sleep_sec=0.6, page_limit=None)
    if df.empty:
        print("종료: 수집 데이터 없음")
        exit()

    # 2) CSV 저장 (GitHub Pages 업데이트/백업용 - 메일에는 첨부하지 않음)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_csv = f"saramin_results_{ts}.csv"
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"✅ CSV 저장 완료: {len(df)} rows → {out_csv}")

    # 3) HTML 저장 (/docs 폴더)
    docs_dir = Path("docs")
    html_path = docs_dir / "saramin_results_latest.html"
    crawler.build_html_page(df, str(html_path))

    # 4) GitHub Pages URL (필요에 따라 수정)
    pages_url = "https://pkpjs.github.io/test/saramin_results_latest.html"

    # 5) 이메일 발송 설정 (환경변수 읽기)
    EMAIL_SENDER   = os.environ.get("EMAIL_SENDER")
    EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER")
    EMAIL_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")
    if not all([EMAIL_SENDER, EMAIL_RECEIVER, EMAIL_PASSWORD]):
        print("❌ 환경 변수(EMAIL_SENDER / EMAIL_RECEIVER / EMAIL_APP_PASSWORD)가 설정되지 않았습니다.")
        print("🔔 GitHub Secrets 또는 실행 환경의 환경 변수 설정을 확인하세요.")
        exit(1)

    # 6) 이메일 발송 (CSV 첨부 없음)
    jobs_list = df.to_dict(orient="records")
    crawler.send_email(
        jobs=jobs_list,
        sender_email=EMAIL_SENDER,
        app_password=EMAIL_PASSWORD,
        receiver_email=EMAIL_RECEIVER,
        pages_url=pages_url
    )

    print(f"🔗 전체 공고 페이지 주소: {pages_url}")
