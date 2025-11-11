# -*- coding: utf-8 -*-
import math, time, os, re, csv, json, requests, smtplib
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ================== AI 가중치 ==================
BIG_FIRM_HINTS = ["대기업","공기업","공사","공단","그룹","삼성","LG","현대","롯데","한화","SK","카카오","네이버","KT","포스코"]
MID_FIRM_HINTS = ["중견","강소","우량"]
DEADLINE_IMMINENT_3D, DEADLINE_IMMINENT_7D, DEADLINE_NONE = 50, 40, 10
FRESH_NEW, FRESH_OLD = 30, -10
FIRM_BIG, FIRM_MID = 15, 10
SALARY_GOOD = 5

def score_job(j):
    score = 0
    deadline = j.get("deadline", "")
    if deadline:
        try:
            m = re.search(r"(\d{2})/(\d{2})", deadline)
            if m:
                dd = datetime(datetime.now().year, int(m.group(1)), int(m.group(2)))
                d = (dd - datetime.now()).days
                if d <= 3: score += DEADLINE_IMMINENT_3D
                elif d <= 7: score += DEADLINE_IMMINENT_7D
        except: pass
    else:
        score += DEADLINE_NONE
    name = j.get("company", "")
    if any(k in name for k in BIG_FIRM_HINTS): score += FIRM_BIG
    elif any(k in name for k in MID_FIRM_HINTS): score += FIRM_MID
    try:
        t = datetime.strptime(j.get("crawled_at", ""), "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - t).days <= 1: score += FRESH_NEW
        else: score += FRESH_OLD
    except: pass
    salary = j.get("salary", "")
    if salary and "협의" not in salary:
        nums = [int(x) for x in re.findall(r'\d{3,4}', salary)]
        if nums and max(nums) >= 3500: score += SALARY_GOOD
    return score


# ================== Saramin Crawler ==================
class SaraminCrawler:
    def __init__(self):
        self.api_url = "https://www.saramin.co.kr/zf_user/search/get-recruit-list"
        self.headers = {
            "User-Agent": "Mozilla/5.0",
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

    def _parse_page(self, html):
        soup = BeautifulSoup(html, "html.parser")
        jobs = []
        for item in soup.select("div.item_recruit"):
            try:
                rec_idx = (item.get("value") or "").strip()
                a = item.select_one("h2.job_tit a")
                if not a: continue
                title = a.get_text(strip=True)
                href = a.get("href", "")
                link = "https://www.saramin.co.kr" + href if href.startswith("/") else href
                corp_el = item.select_one("strong.corp_name a, strong.corp_name")
                company = corp_el.get_text(strip=True) if corp_el else ""
                info = item.select("div.job_condition span")
                location = info[0].get_text(strip=True) if len(info)>0 else ""
                career = info[1].get_text(strip=True) if len(info)>1 else ""
                edu = info[2].get_text(strip=True) if len(info)>2 else ""
                deadline_el = item.select_one("div.job_date span.date")
                deadline = deadline_el.get_text(strip=True) if deadline_el else ""
                jobs.append({
                    "rec_idx": rec_idx, "title": title, "company": company,
                    "location": location, "career": career, "education": edu,
                    "deadline": deadline, "link": link, "salary": "",
                    "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
            except: continue
        return jobs

    def _fetch(self, page):
        p = dict(self.params)
        p["recruitPage"]=page
        r = requests.get(self.api_url, params=p, headers=self.headers)
        data = r.json()
        html = data.get("innerHTML","")
        cnt = int(str(data.get("count","0")).replace(",","") or 0)
        return self._parse_page(html), cnt

    def crawl_all(self):
        all_jobs, first = [], None
        first, total = self._fetch(1)
        if not first: return pd.DataFrame()
        all_jobs.extend(first)
        pages = math.ceil(total / int(self.params["recruitPageCount"]))
        for p in range(2, pages+1):
            jobs,_ = self._fetch(p)
            if not jobs: break
            all_jobs.extend(jobs)
            time.sleep(0.4)
        df = pd.DataFrame(all_jobs)
        if df.empty: return df
        df.drop_duplicates(subset=["rec_idx"], inplace=True)
        df["score"] = df.apply(score_job, axis=1)
        df = df.sort_values("score",ascending=False).reset_index(drop=True)
        return df

    # ✅ 지원완료 컬럼 포함 HTML 생성
    def build_html(self, df, path):
        p = Path(path); p.parent.mkdir(exist_ok=True, parents=True)
        if "status" not in df.columns: df["status"] = ""
        if "applied_at" not in df.columns: df["applied_at"] = ""
        html = """
        <html><head><meta charset="UTF-8">
        <style>
        body{font-family:"Pretendard","Apple SD Gothic Neo",sans-serif;background:#f9fafb;margin:0;}
        h2{text-align:center;color:#2563eb;padding:20px 0;}
        .card{background:white;margin:12px auto;padding:16px 20px;max-width:600px;
        border-radius:10px;box-shadow:0 2px 6px rgba(0,0,0,0.05);}
        .title{font-weight:600;color:#1d4ed8;}
        .company{margin-top:4px;}
        .meta{font-size:13px;color:#555;margin-top:6px;}
        .status{font-size:0.9rem;font-weight:600;color:#16a34a;margin-top:8px;}
        .button{display:inline-block;margin-top:10px;padding:8px 14px;background:#2563eb;color:#fff;
        border-radius:6px;text-decoration:none;}
        </style></head><body><h2>🎯 AI 추천 채용공고</h2>
        """
        for _, r in df.iterrows():
            status_html = f"<div class='status'>✅ 지원완료 ({r['applied_at']})</div>" if r["status"]=="applied" else ""
            html += f"""
            <div class="card">
              <div class="title">{r['title']}</div>
              <div class="company">{r['company']}</div>
              <div class="meta">{r['location']} · {r['career']} · {r['education']} · 마감일: {r['deadline']} · 점수: {r['score']}</div>
              {status_html}
              <a href="{r['link']}" class="button" target="_blank">🔗 공고 바로가기</a>
            </div>
            """
        html += "</body></html>"
        p.write_text(html, encoding="utf-8")
        print(f"✅ HTML 생성 완료 → {path}")


# ✅ Gmail에서 지원완료 반영 (Secrets + 로컬 대응)
def update_from_mail(csv_path):
    SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
    token_env = os.getenv("GOOGLE_TOKEN_JSON")
    if token_env:
        print("✅ Using Gmail token from GitHub Secret")
        creds = Credentials.from_authorized_user_info(json.loads(token_env), SCOPES)

    service = build('gmail', 'v1', credentials=creds)
    query = '(subject:"입사지원 완료" OR subject:"지원이 완료되었습니다" OR subject:"성공적으로 완료되었습니다")'
    results = service.users().messages().list(userId='me', q=query, maxResults=10).execute()
    messages = results.get('messages', [])
    if not messages:
        print("📭 새 지원완료 메일 없음.")
        return pd.read_csv(csv_path)

    df = pd.read_csv(csv_path, encoding='utf-8')
    if "status" not in df.columns: df["status"] = ""
    if "applied_at" not in df.columns: df["applied_at"] = ""

    for m in messages:
        msg = service.users().messages().get(userId='me', id=m['id']).execute()
        subject = next((h['value'] for h in msg['payload']['headers'] if h['name'] == 'Subject'), "")
        match = re.search(r"\[사람인\]\s*(.+?)에\s*입사지원이\s*(?:성공적으로\s*)?완료", subject)
        if not match: continue
        company = match.group(1).strip()
        print(f"📨 지원완료 메일 감지: {company}")
        mask = df["company"].str.contains(company, na=False)
        df.loc[mask, "status"] = "applied"
        df.loc[mask, "applied_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print("✅ CSV 상태 업데이트 완료")
    return df


def clean_old_csv():
    files = sorted(Path(".").glob("saramin_results_*.csv"), key=lambda x: x.stat().st_mtime, reverse=True)
    for f in files[1:]: os.remove(f)


# ================= MAIN ==================
if __name__ == "__main__":
    crawler = SaraminCrawler()
    df = crawler.crawl_all()
    if df.empty:
        print("❌ 데이터 없음"); exit()

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = f"saramin_results_{ts}.csv"
    df.to_csv(csv_path,index=False,encoding="utf-8-sig")
    print(f"✅ CSV 저장: {csv_path}")
    clean_old_csv()

    # ✅ Gmail에서 지원완료 메일 반영
    df = update_from_mail(csv_path)

    # ✅ 반영된 데이터로 HTML 생성
    html_path = "docs/saramin_results_latest.html"
    crawler.build_html(df, html_path)

    # ✅ 이메일 전송
    EMAIL_SENDER = os.getenv("EMAIL_SENDER")
    EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
    EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")
    if not all([EMAIL_SENDER, EMAIL_RECEIVER, EMAIL_APP_PASSWORD]):
        print("❌ 이메일 env 없음"); exit()

    top10 = df.head(10).to_dict(orient="records")
    msg = MIMEMultipart('alternative')
    msg['From'], msg['To'] = EMAIL_SENDER, EMAIL_RECEIVER
    msg['Subject'] = f"🎯 AI 추천 채용공고 - {datetime.now().strftime('%Y-%m-%d')}"
    html = "<h2>🎯 AI 추천 TOP 10 채용공고</h2>"
    for j in top10:
        status_txt = f"<div style='color:green;'>✅ 지원완료 ({j.get('applied_at','')})</div>" if j.get('status')=="applied" else ""
        html += f"""
        <div style='border:1px solid #eee;border-radius:8px;padding:10px;margin:8px;'>
        <b>{j['title']}</b> - {j['company']}<br>
        {j['location']} · {j['career']} · 마감: {j['deadline']} · 점수: {j['score']}<br>
        {status_txt}
        <a href="{j['link']}">🔗 공고 보기</a>
        </div>"""
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    s = smtplib.SMTP('smtp.gmail.com', 587); s.starttls()
    s.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
    s.send_message(msg); s.quit()
    print("📧 이메일 전송 완료!")
