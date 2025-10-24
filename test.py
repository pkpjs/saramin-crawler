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
from email.mime.application import MIMEApplication
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
        self.params = {
            "searchType": "search",
            "loc_mcd": "106000,104000,105000,107000,110000,111000",
            "cat_kewd": "83,84,85,90,104,108,111,112,114,116",
            "company_cd": "0,1,2,3,4,5,6,7,9,10",
            "exp_cd": "1",
            "exp_none": "y",
            "job_type": "1",
            "search_optional_item": "y",
            "search_done": "y",
            "panel_count": "y",
            "preview": "y",
            "recruitPage": 1,
            "recruitPageCount": 40,
            "recruitSort": "relation"
        }

    def _parse_jobs_from_innerHTML(self, inner_html):
        soup = BeautifulSoup(inner_html, "html.parser")
        jobs = []
        for item in soup.select("div.item_recruit"):
            try:
                rec_idx = item.get("value", "").strip()
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
        except:
            total_count = 0
        jobs = self._parse_jobs_from_innerHTML(inner_html) if inner_html else []
        return jobs, total_count

    def crawl_all(self, sleep_sec=0.6, page_limit=None):
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
        if "rec_idx" in df.columns:
            df["__dedup_key"] = df["rec_idx"].where(df["rec_idx"].astype(bool), other=df["link"])
            df.drop_duplicates(subset=["__dedup_key"], inplace=True)
            df.drop(columns=["__dedup_key"], inplace=True)
        else:
            df.drop_duplicates(subset=["link"], inplace=True)
        return df

    def _get_keyword_stats(self, jobs):
        from collections import Counter
        cnt = Counter([j.get("keyword", "기타") for j in jobs])
        return ", ".join([f"{k}({v}개)" for k, v in cnt.items()]) if cnt else "없음"

    def build_results_page(self, df: pd.DataFrame, out_html_path: str, page_title: str = "Saramin Results"):
        """CSV DataFrame을 HTML 페이지로 변환하여 저장 (/docs 아래 권장)"""
        out_path = Path(out_html_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # 보기 좋은 테이블 생성
        styled = (
            df[['title','company','location','career','education','deadline','link','crawled_at']]
            .rename(columns={
                'title':'제목','company':'회사','location':'위치',
                'career':'경력','education':'학력','deadline':'마감일',
                'link':'링크','crawled_at':'수집시각'
            })
        )

        table_html = styled.to_html(index=False, escape=False, justify="center", border=0)

        # 심플한 CSS 포함 페이지
        html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{page_title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ font-size: 24px; margin-bottom: 8px; }}
.desc {{ color:#666; margin-bottom: 20px; }}
table {{ width:100%; border-collapse: collapse; }}
th, td {{ border-bottom: 1px solid #eee; padding: 10px 8px; text-align: left; vertical-align: top; }}
th {{ background:#fafafa; font-weight: 700; }}
tr:hover {{ background: #fffdf4; }}
a {{ text-decoration: none; }}
.btn {{ display:inline-block; padding:10px 16px; border-radius:8px; border:1px solid #ddd; }}
.meta {{ color:#888; font-size: 13px; margin-top: 10px; }}
</style>
</head>
<body>
<div class="container">
  <h1>채용공고 결과</h1>
  <div class="desc">생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
  {table_html}
  <div class="meta">Powered by Python · 자동 크롤링</div>
</div>
</body>
</html>"""
        out_path.write_text(html, encoding="utf-8")
        print(f"🌐 HTML 생성: {out_path}")
        return str(out_path)

    def send_email(self, jobs, csv_path, sender_email, app_password, receiver_email, pages_url: str = None):
        if not jobs:
            print("⚠ 전송할 공고가 없습니다.")
            return

        subject = f"🎯 채용공고 자동 수집 결과 - {datetime.now().strftime('%Y-%m-%d')}"
        # 상단 요약+미리보기
        html_body = f"""
        <html>
            <head>
                <meta charset="UTF-8">
                <style>
                    body {{ font-family: 'Apple SD Gothic Neo', Arial, sans-serif; }}
                    .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                            color: white; padding: 20px; text-align: center; }}
                    .job-item {{ border: 1px solid #ddd; margin: 10px 0; padding: 15px; 
                            border-radius: 8px; background: #fafafa; }}
                    .job-title {{ font-size: 18px; font-weight: bold; color: #2c3e50; }}
                    .company {{ color: #e74c3c; font-weight: bold; margin: 5px 0; }}
                    .details {{ color: #7f8c8d; font-size: 14px; margin: 5px 0; }}
                    .btn {{ background: #3498db; color: white; padding: 10px 18px; 
                        text-decoration: none; border-radius: 6px; display: inline-block; }}
                    .summary {{ background: #ecf0f1; padding: 15px; margin: 20px 0; border-radius: 8px; }}
                </style>
            </head>
            <body>
                <div class="header">
                    <h1>🎯 채용공고 자동 수집 결과</h1>
                    <p>{datetime.now().strftime('%Y년 %m월 %d일')} 수집 완료</p>
                </div>
                
                <div class="summary">
                    <h2>📊 수집 현황</h2>
                    <p>• <strong>총 {len(jobs)}개</strong> 공고 발견</p>
                    <p>• 📎 전체 데이터는 첨부된 CSV 또는 아래 버튼을 눌러 웹페이지에서 확인하세요!</p>
                </div>
        """

        # 버튼으로 GitHub Pages 링크 제공
        if pages_url:
            html_body += f"""
                <div style="text-align:center; margin: 18px 0 28px;">
                    <a class="btn" href="{pages_url}" target="_blank">🌐 전체 공고 보기 (GitHub Pages)</a>
                </div>
            """

        # 미리보기 10개
        for job in jobs[:10]:
            html_body += f"""
            <div class="job-item">
                <div class="job-title">{job.get('title','')}</div>
                <div class="company">🏢 {job.get('company','')}</div>
                <div class="details">
                    📍 {job.get('location','')} | 
                    👔 {job.get('career','')} | 
                    🎓 {job.get('education','')} | 
                    ⏰ {job.get('deadline','')}
                </div>
                <a href="{job.get('link','')}" class="btn" target="_blank">지원하기 →</a>
            </div>
            """

        html_body += """
                <div style="text-align: center; margin-top: 30px; padding: 20px; background: #f8f9fa;">
                    <p>🤖 Python 자동화 시스템이 수집했습니다</p>
                </div>
            </body>
        </html>
        """

        msg = MIMEMultipart('alternative')
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = subject
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        # CSV 첨부
        if csv_path and os.path.exists(csv_path):
            with open(csv_path, 'rb') as f:
                part = MIMEApplication(f.read(), _subtype='csv')
                part.add_header('Content-Disposition', 'attachment',
                                filename=os.path.basename(csv_path))
                msg.attach(part)

        try:
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(sender_email, app_password)
            server.send_message(msg)
            server.quit()
            print("📧 이메일 전송 완료!")
        except Exception as e:
            print(f"❌ 이메일 전송 실패: {e}")

if __name__ == "__main__":
    crawler = SaraminCrawler()

    # 1) 크롤링
    df = crawler.crawl_all(sleep_sec=0.6, page_limit=None)
    if df.empty:
        print("종료: 수집 데이터 없음")
        raise SystemExit(0)

    # 2) CSV 저장
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_csv = f"saramin_results_{ts}.csv"
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"✅ CSV 저장 완료: {len(df)} rows → {out_csv}")

    # 3) HTML 저장 (/docs)
    docs_dir = Path("docs")
    latest_html = docs_dir / "saramin_results_latest.html"
    versioned_html = docs_dir / f"saramin_results_{ts}.html"

    crawler.build_results_page(df, str(latest_html), page_title="Saramin Results (Latest)")
    crawler.build_results_page(df, str(versioned_html), page_title=f"Saramin Results ({ts})")

    # 4) GitHub Pages URL 생성 (Actions 환경변수 사용)
    # GITHUB_REPOSITORY = "{owner}/{repo}"
    gh_repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in gh_repo:
        owner, repo = gh_repo.split("/", 1)
    else:
        owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
        repo = "test"  # fallback
    # 저장소명이 'test', 브랜치 main 가정 (GitHub Pages는 /docs 기준 자동배포)
    pages_url = f"https://{owner}.github.io/{repo}/saramin_results_latest.html"

    # 5) 이메일 환경변수 (Secrets)
    EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
    EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER")
    EMAIL_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")
    if not all([EMAIL_SENDER, EMAIL_RECEIVER, EMAIL_PASSWORD]):
        print("❌ 환경 변수(EMAIL_SENDER / EMAIL_RECEIVER / EMAIL_APP_PASSWORD)가 설정되지 않았습니다.")
        print("🔔 GitHub Secrets 또는 환경 변수 설정을 확인하세요.")
        raise SystemExit(1)

    # 6) 이메일 발송
    jobs_list = df.to_dict(orient="records")
    crawler.send_email(
        jobs=jobs_list,
        csv_path=out_csv,
        sender_email=EMAIL_SENDER,
        app_password=EMAIL_PASSWORD,
        receiver_email=EMAIL_RECEIVER,
        pages_url=pages_url
    )

    print(f"🔗 GitHub Pages URL: {pages_url}")
