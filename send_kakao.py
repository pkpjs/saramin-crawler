# -*- coding: utf-8 -*-
import os, re, json, requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime, timedelta, timezone

# ===== 기본 설정 =====
KST = timezone(timedelta(hours=9))
REST_API_KEY  = os.getenv("KAKAO_REST_API_KEY")
REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN")
PAGES_URL     = os.getenv("PAGES_URL", "https://pkpjs.github.io/test/saramin_results_latest.html")
HTML_PATH     = "docs/saramin_results_latest.html"
STATE_PATH    = "docs/last_rec_ids.json"
SARAMIN_BASE  = "https://www.saramin.co.kr"

# ===== 점수 설정 =====
DEADLINE_IMMINENT_3D = 50
DEADLINE_IMMINENT_7D = 40
DEADLINE_NONE        = 10
FRESH_NEW            = 30
FRESH_OLD            = -10
FIRM_BIG             = 15
FIRM_MID             = 10
SALARY_GOOD          = 5

BIG_FIRM_HINTS = ["대기업","공기업","공사","공단","그룹","삼성","LG","현대","롯데","한화","SK","카카오","네이버","KT","포스코"]
MID_FIRM_HINTS = ["중견","강소","우량"]

# ===== Access Token 갱신 =====
def refresh_access_token():
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": REST_API_KEY,
        "refresh_token": REFRESH_TOKEN
    }
    r = requests.post(url, data=data)
    r.raise_for_status()
    js = r.json()
    if "access_token" not in js:
        raise RuntimeError(f"토큰 갱신 실패: {js}")
    return js["access_token"]

# ===== HTML 불러오기 =====
def load_html_text():
    try:
        with open(HTML_PATH, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except FileNotFoundError:
        r = requests.get(PAGES_URL)
        r.raise_for_status()
        return r.text

# ===== 마감일 파싱 =====
def parse_deadline(text):
    if not text: return None
    t = text.strip()
    if any(k in t for k in ["상시", "수시", "채용시"]): return None
    m = re.search(r'(\d{1,2})[./-](\d{1,2})', t) or re.search(r'(\d{1,2})월\s*(\d{1,2})일', t)
    if not m: return None
    month, day = int(m.group(1)), int(m.group(2))
    now = datetime.now(KST)
    year = now.year
    d = datetime(year, month, day, tzinfo=KST)
    # 연도 보정
    if d < now - timedelta(days=180): 
        d = datetime(year+1, month, day, tzinfo=KST)
    return d

def days_to_deadline(dl):
    if not dl: return None
    return (dl.date() - datetime.now(KST).date()).days

# ===== 공고 파싱 =====
def extract_items():
    html = load_html_text()
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if not table: return [], 0

    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    rows = table.find_all("tr")[1:]
    total = len(rows)

    def idx(name):
        try: return headers.index(name)
        except: return None

    i_title = idx("제목") or idx("링크")
    i_company = idx("회사")
    i_loc = idx("위치")
    i_job = idx("직무")
    i_dead = idx("마감일") or idx("마감")
    i_salary = idx("연봉") or idx("급여")
    i_direct = idx("바로가기")

    items = []
    for tr in rows:
        tds = tr.find_all("td")
        if not tds: continue

        title, url = "", ""
        if i_title is not None:
            a = tds[i_title].find("a", href=True)
            if a:
                title = a.get_text(strip=True)
                href = a["href"]
                url = href if href.startswith("http") else urljoin(SARAMIN_BASE, href)

        if not url and i_direct is not None:
            a2 = tds[i_direct].find("a", href=True)
            if a2:
                href2 = a2["href"]
                url = href2 if href2.startswith("http") else urljoin(SARAMIN_BASE, href2)

        company = tds[i_company].get_text(strip=True) if i_company is not None else ""
        loc = tds[i_loc].get_text(strip=True) if i_loc is not None else ""
        job = tds[i_job].get_text(strip=True) if i_job is not None else ""
        deadline_text = tds[i_dead].get_text(strip=True) if i_dead is not None else ""
        salary = tds[i_salary].get_text(strip=True) if i_salary is not None else ""
        rec_idx = re.search(r"rec_idx=(\d+)", url).group(1) if "rec_idx=" in url else None

        items.append({
            "title": title or "(제목없음)",
            "company": company,
            "location": loc,
            "job": job,
            "deadline_text": deadline_text,
            "deadline": parse_deadline(deadline_text),
            "salary": salary,
            "url": url or PAGES_URL,
            "rec_idx": rec_idx
        })
    return items, total

# ===== 점수 계산 =====
def load_last_ids():
    try:
        with open(STATE_PATH, "r") as f:
            return set(json.load(f))
    except:
        return set()

def save_current_ids(items):
    ids = [it["rec_idx"] for it in items if it["rec_idx"]]
    with open(STATE_PATH, "w") as f:
        json.dump(ids, f)

def deadline_score(dl):
    d = days_to_deadline(dl)
    if d is None: return DEADLINE_NONE
    if d <= 3: return DEADLINE_IMMINENT_3D
    if d <= 7: return DEADLINE_IMMINENT_7D
    return max(0, 30 - min(d, 30))

def freshness_score(item, last_ids):
    return FRESH_NEW if item["rec_idx"] and item["rec_idx"] not in last_ids else FRESH_OLD

def firm_score(name):
    n = name.lower()
    if any(k.lower() in n for k in BIG_FIRM_HINTS): return FIRM_BIG
    if any(k.lower() in n for k in MID_FIRM_HINTS): return FIRM_MID
    return 0

def salary_score(text):
    if not text or "협의" in text: return 0
    nums = [int(x) for x in re.findall(r'\d{3,4}', text)]
    return SALARY_GOOD if nums and max(nums) >= 3500 else 0

def calc_score(item, last_ids):
    return (
        deadline_score(item["deadline"]) +
        freshness_score(item, last_ids) +
        firm_score(item["company"]) +
        salary_score(item["salary"])
    )

def rank_top5():
    items, total = extract_items()
    last_ids = load_last_ids()
    for it in items:
        it["score"] = calc_score(it, last_ids)
    items.sort(key=lambda x: x["score"], reverse=True)
    save_current_ids(items)
    return items[:5], total

# ===== 피드 메시지 전송 (피드 카드형) =====
def send_feed_item(access_token, rank, item):
    title_text = f"{rank}위 ({item['score']}점) | {item['company']} / {item['job']}"
    desc_text = f"{item['location']} | 마감: {item['deadline_text']}"

    template_object = {
        "object_type": "feed",
        "content": {
            "title": title_text,
            "description": desc_text,
            "link": {"web_url": item["url"], "mobile_web_url": item["url"]},
        },
        "buttons": [
            {
                "title": "공고 보러가기 🔗",
                "link": {"web_url": item["url"], "mobile_web_url": item["url"]}
            }
        ]
    }

    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {access_token}"}
    data = {"template_object": json.dumps(template_object, ensure_ascii=False)}
    resp = requests.post(url, headers=headers, data=data)
    print(f"{rank}위 전송 결과:", resp.json())

def send_header_message(access_token, total):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    text = f"📅 {today} AI 추천 TOP 5 (마감>신규>기업>연봉)\n총 {total}건 중 가장 추천되는 5개 공고입니다."
    
    template_object = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL},
        "button_title": "전체 공고 보기"
    }

    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {access_token}"}
    data = {"template_object": json.dumps(template_object, ensure_ascii=False)}
    resp = requests.post(url, headers=headers, data=data)
    print("헤더 전송 결과:", resp.json())

# ===== 메인 실행 =====
def main():
    access_token = refresh_access_token()
    top5, total = rank_top5()

    send_header_message(access_token, total)
    for idx, item in enumerate(top5, start=1):
        send_feed_item(access_token, idx, item)

if __name__ == "__main__":
    main()
