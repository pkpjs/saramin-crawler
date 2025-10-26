# -*- coding: utf-8 -*-
import os, json, requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re

# ===== 기본 설정 =====
KST = timezone(timedelta(hours=9))
REST_API_KEY  = os.getenv("KAKAO_REST_API_KEY")
REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN")
PAGES_URL     = os.getenv("PAGES_URL", "https://pkpjs.github.io/test/saramin_results_latest.html")
HTML_PATH     = "docs/saramin_results_latest.html"
STATE_PATH    = "docs/last_rec_ids.json"
SARAMIN_BASE  = "https://www.saramin.co.kr"

# 점수 관련 설정
DEADLINE_IMMINENT_3D = 50
DEADLINE_IMMINENT_7D = 40
DEADLINE_NONE = 10
FRESH_NEW = 30
FRESH_OLD = -10
REGION_HIT = 20
FIRM_BIG = 15
FIRM_MID = 10
SALARY_GOOD = 5

BIG_FIRM_HINTS = ["대기업","공기업","공사","공단","그룹","삼성","LG","현대","롯데","한화","SK","카카오","네이버","KT","포스코"]
MID_FIRM_HINTS = ["중견","강소","우량"]

# ===== 스마트 토큰 갱신 =====
def refresh_access_token():
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": REST_API_KEY,
        "refresh_token": REFRESH_TOKEN
    }
    resp = requests.post(url, data=data)
    resp.raise_for_status()
    js = resp.json()
    if "access_token" not in js:
        raise Exception(f"Access Token 갱신 실패: {js}")
    return js["access_token"]

# ===== HTML 로드 =====
def load_html_text():
    try:
        with open(HTML_PATH, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        resp = requests.get(PAGES_URL)
        resp.raise_for_status()
        return resp.text

# ===== 마감일 파싱 =====
def parse_deadline(text):
    if not text: return None
    t = text.strip()
    if any(k in t for k in ["상시","수시","채용시"]): return None
    m = re.search(r'(\d{1,2})[./-](\d{1,2})', t) or re.search(r'(\d{1,2})월\s*(\d{1,2})일', t)
    if not m: return None
    month, day = int(m.group(1)), int(m.group(2))
    now = datetime.now(KST)
    year = now.year
    d = datetime(year, month, day, tzinfo=KST)
    if d < now - timedelta(days=180):
        d = datetime(year+1, month, day, tzinfo=KST)
    return d

def days_to_deadline(d):
    if d is None: return None
    return (d.date() - datetime.now(KST).date()).days

# ===== 표 파싱 =====
def extract_items():
    html = load_html_text()
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if not table: return [], 0

    headers = [th.get_text(strip=True) for th in table.select("thead tr th")]
    rows = table.select("tbody tr")
    total = len(rows)

    def idx(*names):
        for n in names:
            if n in headers: return headers.index(n)
        return None

    i_link = idx("링크","제목","title")
    i_company = idx("회사","company")
    i_loc = idx("위치","location")
    i_job = idx("직무","job")
    i_dead = idx("마감일","마감","deadline")
    i_direct = idx("바로가기")
    i_salary = idx("연봉","급여","salary")

    items = []
    for tr in rows:
        tds = tr.find_all("td")
        if not tds: continue

        title, url = "", ""
        if i_link is not None:
            a = tds[i_link].find("a", href=True)
            if a:
                title = a.get_text(strip=True)
                href  = a["href"]
                url   = href if href.startswith("http") else urljoin(SARAMIN_BASE, href)
        if not url and i_direct is not None:
            a2 = tds[i_direct].find("a", href=True)
            if a2:
                href2 = a2["href"]
                url = href2 if href2.startswith("http") else urljoin(SARAMIN_BASE, href2)

        company = tds[i_company].get_text(strip=True) if i_company is not None else ""
        loc     = tds[i_loc].get_text(strip=True) if i_loc is not None else ""
        job     = tds[i_job].get_text(strip=True) if i_job is not None else ""
        deadline_text = tds[i_dead].get_text(strip=True) if i_dead is not None else ""
        salary  = tds[i_salary].get_text(strip=True) if i_salary is not None else ""
        rec_idx = re.search(r"rec_idx=(\d+)", url).group(1) if "rec_idx=" in url else None

        items.append({
            "title": title,
            "company": company,
            "location": loc,
            "job": job,
            "deadline_text": deadline_text,
            "deadline": parse_deadline(deadline_text),
            "salary": salary,
            "url": url,
            "rec_idx": rec_idx
        })
    return items, total

# ===== 점수 계산 =====
def firm_score(name):
    low = name.lower()
    if any(k.lower() in low for k in BIG_FIRM_HINTS): return FIRM_BIG
    if any(k.lower() in low for k in MID_FIRM_HINTS): return FIRM_MID
    return 0

def salary_score(text):
    if not text or "협의" in text: return 0
    nums = [int(x) for x in re.findall(r'\d{3,4}', text)]
    return SALARY_GOOD if nums and max(nums) >= 3500 else 0

def deadline_score(deadline):
    d = days_to_deadline(deadline)
    if d is None: return DEADLINE_NONE
    if d <= 3: return DEADLINE_IMMINENT_3D
    if d <= 7: return DEADLINE_IMMINENT_7D
    return max(0, 30 - min(d, 30))  # 멀면 점수 낮음

def freshness_score(item):
    return FRESH_NEW if item["rec_idx"] and item["rec_idx"] not in load_last_ids() else FRESH_OLD

def load_last_ids():
    try:
        with open(STATE_PATH, "r") as f:
            return set(json.load(f))
    except:
        return set()

def save_current_ids(items):
    ids = [x["rec_idx"] for x in items if x["rec_idx"]]
    with open(STATE_PATH, "w") as f:
        json.dump(ids, f)

def rank_items(items):
    last_ids = load_last_ids()
    for it in items:
        it["score"] = 0
        it["score"] += deadline_score(it["deadline"])
        it["score"] += freshness_score(it)
        it["score"] += firm_score(it["company"])
        it["score"] += salary_score(it["salary"])
    items.sort(key=lambda x: x["score"], reverse=True)
    save_current_ids(items)
    return items[:5]

# ===== 리스트형 메시지 전송 =====
def send_list_template(access_token, top5, total):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    header_text = f"📅 {today} AI 추천 TOP5 (마감>신규>기업>연봉)\n총 {total}건 중 추천된 상위 5개입니다."

    list_items = []
    for idx, item in enumerate(top5, start=1):
        title = f"{idx}위 ({item['score']}점) | {item['company']}"
        desc = f"{item['location']} | {item['job']} | 마감: {item['deadline_text']}"
        list_items.append({
            "title": title,
            "description": desc,
            "link": {"web_url": item['url'], "mobile_web_url": item['url']}
        })

    template_object = {
        "object_type": "list",
        "header_title": header_text,
        "header_link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL},
        "contents": list_items,
        "buttons": [{"title": "전체 공고 보기 🔗", "link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL}}]
    }

    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {access_token}"}
    data = {"template_object": json.dumps(template_object, ensure_ascii=False)}
    resp = requests.post(url, headers=headers, data=data)
    print("전송 결과:", resp.json())

def main():
    access_token = refresh_access_token()
    items, total = extract_items()
    top5 = rank_items(items)
    send_list_template(access_token, top5, total)

if __name__ == "__main__":
    main()
