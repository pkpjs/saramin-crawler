# -*- coding: utf-8 -*-
import math
import time
import os
import re
import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

# ================== AI 가중치 설정 ==================
BIG_FIRM_HINTS = ["대기업","공기업","공사","공단","그룹","삼성","LG","현대","롯데","한화","SK","카카오","네이버","KT","포스코"]
MID_FIRM_HINTS = ["중견","강소","우량"]

DEADLINE_IMMINENT_3D = 50
DEADLINE_IMMINENT_7D = 40
DEADLINE_NONE = 10
FRESH_NEW = 30
FRESH_OLD = -10
FIRM_BIG = 15
FIRM_MID = 10
SALARY_GOOD = 5


# ================== AI 점수 계산 함수 ==================
def score_job(j):
    score = 0

    # 마감일 점수
    deadline = j.get("deadline", "")
    if deadline:
        try:
            m = re.search(r"(\d{2})/(\d{2})", deadline)
            if m:
                dd = datetime(datetime.now().year, int(m.group(1)), int(m.group(2)))
                diff = (dd - datetime.now()).days
                if diff <= 3: score += DEADLINE_IMMINENT_3D
                elif diff <= 7: score += DEADLINE_IMMINENT_7D
        except:
            pass
    else:
        score += DEADLINE_NONE

    # 회사 규모 점수
    name = j.get("company", "")
    if any(k in name for k in BIG_FIRM_HINTS): score += FIRM_BIG
    elif any(k in name for k in MID_FIRM_HINTS): score += FIRM_MID

    # 신규 공고 점수
    try:
        t = datetime.strptime(j.get("crawled_at",""), "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - t).days <= 1: score += FRESH_NEW
        else: score += FRESH_OLD
    except:
        pass

    # 연봉 점수
    salary = j.get("salary","")
    if salary and "협의" not in salary:
        nums = [int(x) for x in re.findall(r'\d{3,4}', salary)]
        if nums and max(nums) >= 3500: score += SALARY_GOOD

    return score


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
            "loc_mcd": "106000,104000,105000,107000,110000,111000",   # 부산/대구/대전/울산/경남/경북
            "cat_kewd": "83,84,85,90,104,108,111,112,114,116",       # 데이터/보안/AI 등
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
                rec_idx = (item.get("value") or "").strip()
                a = item.select_one("h2.job_tit a")
                if not a: continue

                title = a.get_text(strip=True)
                href = a.get("href", "")
                link = "https://www.saramin.co.kr" + href if href.startswith("/") else href

                company_el = item.select_one("strong.corp_name a, strong.corp_name")
                company = company_el.get_text(strip=True) if company_el else ""

                cond_spans = item.select("div.job_condition span")
                location = cond_spans[0].get_text(strip=True) if len(cond_spans)>0 else ""
                career   = cond_spans[1].get_text(strip=True) if len(cond_spans)>1 else ""
                edu      = cond_spans[2].get_text(strip=True) if len(cond_spans)>2 else ""

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
                    "salary": "",
                    "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
            except:
                continue
        return jobs

    def _fetch_page(self, page):
        params = dict(self.params)
        params["recruitPage"] = page
        resp = requests.get(self.api_url, params=params, headers=self.headers)
        data = resp.json()

        inner_html = data.get("innerHTML", "")
        count_str = data.get("count", "0")
        try:
            total_count = int(str(count_str).replace(",", ""))
        except:
            total_count = 0

        jobs = self._parse_jobs_from_innerHTML(inner_html) if inner_html else []
        return jobs, total_count

    def crawl_all(self, sleep_sec=0.5):
        all_jobs = []
        first, total = self._fetch_page(1)
        if not first: return pd.DataFrame()
        all_jobs.extend(first)

        page_count = math.ceil(total / int(self.params["recruitPageCount"]))
        for p in range(2, page_count + 1):
            jobs,_ = self._fetch_page(p)
            if not jobs: break
            all_jobs.extend(jobs)
            time.sleep(sleep_sec)

        df = pd.DataFrame(all_jobs)
        if df.empty: return df

        # dedupe
        df.drop_duplicates(subset=["rec_idx"], inplace=True)

        # ✅ AI 점수 계산 & 정렬 추가
        df["score"] = df.apply(score_job, axis=1)
        df = df.sort_values("score", ascending=False).reset_index(drop=True)

        return df

    def build_html_page(self, df, out_html_path):
        out = Path(out_html_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        styled = df[['title','company','location','career','education','deadline','link','crawled_at','score']].copy()
        styled.rename(columns={
            'title':'제목','company':'회사','location':'위치','career':'경력',
            'education':'학력','deadline':'마감일','link':'링크','crawled_at':'수집시각','score':'점수'
        }, inplace=True)
        styled['링크'] = styled['링크'].apply(lambda x: f'<a href="{x}" target="_blank">바로가기</a>')

        html = styled.to_html(index=False, escape=False)
        out.write_text(f"<h2>채용공고 (AI 추천 순)</h2>{html}", encoding="utf8")
        print(f"🌐 HTML 생성: {out}")

    def send_email(self, jobs, sender, pw, receiver, pages_url):
        if not jobs: return
        html = "<h2>상위 10개 공고 (AI 정렬)</h2><ul>"
        for j in jobs[:10]:
            html += f"<li><b>{j['title']}</b> - {j['company']} ({j['location']})</li>"
        html += f"</ul><br><a href='{pages_url}'>전체보기</a>"

        msg = MIMEText(html, 'html', 'utf-8')
        msg['Subject'] = "Saramin AI 추천 결과"
        msg['From'] = sender
        msg['To'] = receiver
        s = smtplib.SMTP('smtp.gmail.com',587)
        s.starttls()
        s.login(sender,pw)
        s.sendmail(sender, receiver, msg.as_string())
        s.quit()
        print("📧 이메일 전송 완료")


# ================== CSV 최신만 유지 ==================
def clean_old_csv():
    files = sorted(Path(".").glob("saramin_results_*.csv"), key=lambda x: x.stat().st_mtime, reverse=True)
    for old in files[1:]: os.remove(old)


# ================== MAIN ==================
if __name__ == "__main__":
    crawler = SaraminCrawler()
    df = crawler.crawl_all()
    if df.empty:
        print("⚠ 수집 실패")
        exit()

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_file = f"saramin_results_{ts}.csv"
    df.to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"✅ CSV 저장: {csv_file}")

    clean_old_csv()

    crawler.build_html_page(df, "docs/saramin_results_latest.html")

    EMAIL_SENDER = os.getenv("EMAIL_SENDER")
    EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
    EMAIL_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")

    jobs = df.to_dict(orient="records")
    crawler.send_email(jobs, EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER,
                       "https://pkpjs.github.io/test/saramin_results_latest.html")
