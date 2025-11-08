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
        except:
            pass
    else:
        score += DEADLINE_NONE

    # 회사 규모
    name = j.get("company", "")
    if any(k in name for k in BIG_FIRM_HINTS): score += FIRM_BIG
    elif any(k in name for k in MID_FIRM_HINTS): score += FIRM_MID

    # 신규 공고 보너스
    try:
        t = datetime.strptime(j.get("crawled_at", ""), "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - t).days <= 1: score += FRESH_NEW
        else: score += FRESH_OLD
    except:
        pass

    # 연봉 점수
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
                if not a:
                    continue
                title = a.get_text(strip=True)
                href = a.get("href", "")
                link = "https://www.saramin.co.kr" + href if href.startswith("/") else href

                corp_el = item.select_one("strong.corp_name a, strong.corp_name")
                company = corp_el.get_text(strip=True) if corp_el else ""

                info = item.select("div.job_condition span")
                location = info[0].get_text(strip=True) if len(info) > 0 else ""
                career = info[1].get_text(strip=True) if len(info) > 1 else ""
                edu = info[2].get_text(strip=True) if len(info) > 2 else ""

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
        p["recruitPage"] = page
        r = requests.get(self.api_url, params=p, headers=self.headers)
        data = r.json()
        html = data.get("innerHTML", "")
        cnt = int(str(data.get("count", "0")).replace(",", "") or 0)
        return self._parse_page(html), cnt

    def crawl_all(self):
        all_jobs, first = [], None
        first, total = self._fetch(1)
        if not first:
            return pd.DataFrame()
        all_jobs.extend(first)

        pages = math.ceil(total / int(self.params["recruitPageCount"]))
        for p in range(2, pages + 1):
            jobs, _ = self._fetch(p)
            if not jobs:
                break
            all_jobs.extend(jobs)
            time.sleep(0.4)

        df = pd.DataFrame(all_jobs)
        if df.empty:
            return df
        df.drop_duplicates(subset=["rec_idx"], inplace=True)

        df["score"] = df.apply(score_job, axis=1)
        df = df.sort_values("score", ascending=False).reset_index(drop=True)
        return df

    # ================== 반응형 HTML 생성 ==================
   def build_html(self, df, path):
    p = Path(path)
    p.parent.mkdir(exist_ok=True, parents=True)

    # JSON용 데이터 정제
    jobs = []
    for _, row in df.iterrows():
        jobs.append({
            "title": row.get("title", ""),
            "company": row.get("company", ""),
            "location": row.get("location", ""),
            "career": row.get("career", ""),
            "edu": row.get("education", ""),
            "deadline": row.get("deadline", ""),
            "score": int(row.get("score", 0)),
            "url": row.get("link", "")
        })

    json_str = json.dumps(jobs, ensure_ascii=False, indent=2)

    # 새 UI 템플릿 읽기 (캔버스 버전 복붙해도 되고, 여기 내장 가능)
    html_template = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI 추천 채용공고 · 새 UI</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = { darkMode: 'class' }
  </script>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css" />
</head>
<body class="bg-slate-50 dark:bg-slate-900 text-slate-900 dark:text-slate-100 font-sans">
  <div class="min-h-screen flex flex-col">
    <header class="sticky top-0 bg-white/80 dark:bg-slate-900/80 backdrop-blur border-b border-slate-200/60 dark:border-slate-700/60">
      <div class="max-w-6xl mx-auto flex items-center justify-between px-4 py-3">
        <h1 class="font-bold text-lg">🎯 AI 추천 채용공고</h1>
        <button id="themeBtn" class="px-3 py-1.5 border border-slate-300 dark:border-slate-700 rounded-xl">🌗</button>
      </div>
    </header>
    <main class="flex-1 max-w-6xl mx-auto p-4" id="grid">불러오는 중...</main>
    <footer class="py-4 text-center text-sm text-slate-500 dark:text-slate-400 border-t border-slate-200 dark:border-slate-700">
      © pkpjs · 마지막 업데이트 {datetime.now().strftime('%Y-%m-%d %H:%M')}
    </footer>
  </div>
  <script type="application/json" id="jobs-data">{json_str}</script>
  <script>
    const jobs = JSON.parse(document.getElementById('jobs-data').textContent);
    const grid = document.getElementById('grid');
    function card(j) {
      return `
      <article class="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-2xl p-4 shadow-sm hover:shadow-md transition">
        <h3 class="font-semibold text-indigo-600 dark:text-indigo-400">${j.title}</h3>
        <p class="text-sm text-slate-600 dark:text-slate-300">${j.company}</p>
        <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">${j.location} · ${j.career} · ${j.edu}</p>
        <div class="mt-2 flex justify-between text-sm">
          <span>마감일: ${j.deadline}</span>
          <span class="font-medium text-indigo-500">점수 ${j.score}</span>
        </div>
        <a href="${j.url}" target="_blank" class="mt-3 inline-block text-center w-full bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl py-2">공고 보기</a>
      </article>`;
    }
    grid.innerHTML = jobs.map(card).join('');
    const themeBtn = document.getElementById('themeBtn');
    themeBtn.onclick = () => document.documentElement.classList.toggle('dark');
  </script>
</body>
</html>
"""
    p.write_text(html_template, encoding="utf-8")
    print(f"✅ 새 UI HTML 생성 완료 → {path}")

    # ================== 이메일 전송 ==================
    def send_email(self, jobs, sender, pw, receiver, pages_url):
        msg = MIMEMultipart('alternative')
        msg['From'] = sender
        msg['To'] = receiver
        msg['Subject'] = f"🎯 AI 추천 채용공고 - {datetime.now().strftime('%Y-%m-%d')}"

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

        msg.attach(MIMEText(html, 'html', 'utf-8'))

        s = smtplib.SMTP('smtp.gmail.com', 587)
        s.starttls()
        s.login(sender, pw)
        s.send_message(msg)
        s.quit()
        print("📧 이메일 전송 완료!")


def clean_old_csv():
    files = sorted(Path(".").glob("saramin_results_*.csv"), key=lambda x: x.stat().st_mtime, reverse=True)
    for f in files[1:]:
        os.remove(f)


# ================= MAIN ==================
if __name__ == "__main__":
    crawler = SaraminCrawler()
    df = crawler.crawl_all()
    if df.empty:
        print("❌ 데이터 없음")
        exit()

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = f"saramin_results_{ts}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"✅ CSV 저장: {csv_path}")

    clean_old_csv()

    html = "docs/saramin_results_latest.html"
    crawler.build_html(df, html)

    EMAIL_SENDER = os.getenv("EMAIL_SENDER")
    EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
    EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")

    if not all([EMAIL_SENDER, EMAIL_RECEIVER, EMAIL_APP_PASSWORD]):
        print("❌ 이메일 env 없음")
        exit()

    crawler.send_email(
        df.to_dict(orient="records"),
        EMAIL_SENDER,
        EMAIL_APP_PASSWORD,
        EMAIL_RECEIVER,
        "https://pkpjs.github.io/test/saramin_results_latest.html"
    )
