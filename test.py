# -*- coding: utf-8 -*-
import math, time, os, re, requests, smtplib
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

# ================== AI 가중치 ==================
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

def score_job(j):
    score = 0

    # 마감일 점수
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

    # 회사 규모
    name = j.get("company","")
    if any(k in name for k in BIG_FIRM_HINTS): score += FIRM_BIG
    elif any(k in name for k in MID_FIRM_HINTS): score += FIRM_MID

    # 신규 공고 보너스
    try:
        t = datetime.strptime(j.get("crawled_at",""), "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - t).days <= 1: score += FRESH_NEW
        else: score += FRESH_OLD
    except: pass

    # 연봉 텍스트 점수
    salary = j.get("salary","")
    if salary and "협의" not in salary:
        nums = [int(x) for x in re.findall(r'\d{3,4}', salary)]
        if nums and max(nums) >= 3500: score += SALARY_GOOD

    return score


class SaraminCrawler:
    def __init__(self):
        self.api_url = "https://www.saramin.co.kr/zf_user/search/get-recruit-list"
        self.headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.saramin.co.kr/zf_user/search",
            "X-Requested-With": "XMLHttpRequest",
        }
        self.params = {
            "searchType":"search",
            "loc_mcd":"106000,104000,105000,107000,110000,111000",
            "cat_kewd":"83,84,85,90,104,108,111,112,114,116",
            "company_cd":"0,1,2,3,4,5,6,7,9,10",
            "exp_cd":"1",
            "exp_none":"y",
            "job_type":"1",
            "search_optional_item":"y",
            "search_done":"y",
            "panel_count":"y",
            "preview":"y",
            "recruitPage":1,
            "recruitPageCount":40,
            "recruitSort":"relation"
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
                href = a.get("href","")
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

    def build_html(self, df, path):
        p = Path(path)
        p.parent.mkdir(exist_ok=True, parents=True)

        styled = df[['title','company','location','career','education','deadline','score','link']].copy()
        styled.rename(columns={
            'title':'제목','company':'회사','location':'위치','career':'경력',
            'education':'학력','deadline':'마감일','score':'점수','link':'링크'
        }, inplace=True)
        styled['링크']=styled['링크'].apply(lambda x: f'<a href="{x}" target="_blank">바로가기</a>')

        html = styled.to_html(index=False,escape=False)
        p.write_text(f"<h2>AI 추천 채용공고</h2>{html}", encoding="utf-8")
        print("✅ HTML 생성")

    def send_email(self, jobs, sender, pw, receiver, pages_url):
        msg = MIMEMultipart('alternative')
        msg['From']=sender
        msg['To']=receiver
        msg['Subject']=f"🎯 AI 추천 채용공고 - {datetime.now().strftime('%Y-%m-%d')}"

        # 📌 Gmail 카드 UI
        html = """
        <html><head><meta charset="UTF-8">
        <style>
        .card{border:1px solid #eee;border-radius:10px;padding:12px 18px;margin-bottom:12px;background:#fafafa;}
        .title{font-weight:600;font-size:15px;color:#1a73e8;}
        .company{font-size:14px;margin-top:4px;}
        .meta{font-size:13px;color:#555;}
        .button{display:inline-block;margin-top:18px;padding:12px 20px;background:#1a73e8;color:#fff!important;border-radius:6px;text-decoration:none;font-weight:600;}
        </style>
        </head><body>
        <h2>🎯 AI 추천 TOP 10 채용공고</h2>
        """

        for j in jobs[:10]:
            html += f"""
            <div class='card'>
              <div class='title'>{j['title']}</div>
              <div class='company'>{j['company']}</div>
              <div class='meta'>{j['location']} · 마감: {j['deadline']} · 점수: {j['score']}</div>
              <a href="{j['link']}" target="_blank">🔗 공고 바로가기</a>
            </div>
            """

        html += f"""
        <div style='text-align:center;'>
            <a class='button' href="{pages_url}" target="_blank">🌐 전체 공고 보기</a>
        </div>
        </body></html>
        """

        msg.attach(MIMEText(html,'html','utf-8'))

        s = smtplib.SMTP('smtp.gmail.com',587)
        s.starttls()
        s.login(sender,pw)
        s.send_message(msg)
        s.quit()
        print("📧 이메일 전송 완료!")


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

    html = "docs/saramin_results_latest.html"
    crawler.build_html(df, html)

    EMAIL_SENDER=os.getenv("EMAIL_SENDER")
    EMAIL_RECEIVER=os.getenv("EMAIL_RECEIVER")
    EMAIL_APP_PASSWORD=os.getenv("EMAIL_APP_PASSWORD")

    if not all([EMAIL_SENDER,EMAIL_RECEIVER,EMAIL_APP_PASSWORD]):
        print("❌ 이메일 env 없음"); exit()

    crawler.send_email(
        df.to_dict(orient="records"),
        EMAIL_SENDER,
        EMAIL_APP_PASSWORD,
        EMAIL_RECEIVER,
        "https://pkpjs.github.io/test/saramin_results_latest.html"
    )
