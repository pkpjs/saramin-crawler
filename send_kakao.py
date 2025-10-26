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
STATE_PATH    = "docs/last_rec_ids.json"  # 신규 판정용 rec_idx 저장
SARAMIN_BASE  = "https://www.saramin.co.kr"

# ===== 점수 기준 =====
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

# ===== HTML 로드 =====
def load_html():
    try:
        with open(HTML_PATH, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except FileNotFoundError:
        r = requests.get(PAGES_URL)
        r.raise_for_status()
        return r.text

# ===== 마감일 파싱 =====
def parse_deadline(text: str):
    if not text: return None
    t = text.strip()
    if any(word in t for word in ["상시", "수시", "채용시"]):
        return None
    m = re.search(r"(\d{1,2})[./-](\d{1,2})", t) or re.search(r"(\d{1,2})월\s*(\d{1,2})일", t)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    now = datetime.now(KST)
    year = now.year
    d = datetime(year, month, day, tzinfo=KST)
    if d < now - timedelta(days=180):
        d = datetime(year + 1, month, day, tzinfo=KST)
    return d

def days_to_deadline(d):
    if not d:
        return None
    return (d.date() - datetime.now(KST).date()).days

# ===== 공고 파싱 =====
def extract_items():
    html = load_html()
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if not table:
        return [], 0

    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    rows = table.find_all("tr")[1:]
    total = len(rows)

    def idx(*names):
        for name in names:
            if name in headers:
                return headers.index(name)
        return None

    i_title   = idx("제목","링크")
    i_company = idx("회사")
    i_loc     = idx("위치")
    i_job     = idx("직무")
    i_dead    = idx("마감일","마감")
    i_salary  = idx("연봉","급여")
    i_direct  = idx("바로가기")

    items = []
    for tr in rows:
        tds = tr.find_all("td")
        if not tds:
            continue

        # 제목 & URL
        title, url = "", ""
        if i_title is not None and i_title < len(tds):
            a = tds[i_title].find("a", href=True)
            if a:
                title = a.get_text(strip=True)
                href = a["href"].strip()
                url = href if href.startswith("http") else urljoin(SARAMIN_BASE, href)

        # 바로가기 URL
        if not url and i_direct is not None and i_direct < len(tds):
            a2 = tds[i_direct].find("a", href=True)
            if a2:
                href2 = a2["href"].strip()
                url = href2 if href2.startswith("http") else urljoin(SARAMIN_BASE, href2)

        company = tds[i_company].get_text(strip=True) if i_company is not None else ""
        loc     = tds[i_loc].get_text(strip=True) if i_loc is not None else ""
        job     = tds[i_job].get_text(strip=True) if i_job is not None else ""
        deadraw = tds[i_dead].get_text(strip=True) if i_dead is not None else ""
        salary  = tds[i_salary].get_text(strip=True) if i_salary is not None else ""
        rec_idx = None
        if url:
            m = re.search(r"rec_idx=(\d+)", url)
            if m:
                rec_idx = m.group(1)

        items.append({
            "title": title or "(제목없음)",
            "company": company,
            "location": loc,
            "job": job,
            "deadline_text": deadraw,
            "deadline": parse_deadline(deadraw),
            "salary": salary,
            "url": url or PAGES_URL,
            "rec_idx": rec_idx
        })
    return items, total

# ===== 점수 계산 =====
def load_last_ids():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except:
        return set()

def save_current_ids(items):
    ids = [x["rec_idx"] for x in items if x.get("rec_idx")]
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(ids, f, ensure_ascii=False)
    except:
        pass

def deadline_score(dl):
    d = days_to_deadline(dl)
    if d is None: return DEADLINE_NONE
    if d <= 3: return DEADLINE_IMMINENT_3D
    if d <= 7: return DEADLINE_IMMINENT_7D
    return max(0, 30 - min(d, 30))

def freshness_score(item, last_ids):
    rec = item.get("rec_idx")
    return FRESH_NEW if rec and rec not in last_ids else FRESH_OLD

def firm_score(name):
    n = (name or "").lower()
    if any(k.lower() in n for k in BIG_FIRM_HINTS): return FIRM_BIG
    if any(k.lower() in n for k in MID_FIRM_HINTS): return FIRM_MID
    return 0

def salary_score(text):
    if not text or "협의" in text:
        return 0
    nums = re.findall(r"\d{3,4}", text)
    if nums and max(map(int, nums)) >= 3500:
        return SALARY_GOOD
    return 0

def total_score(item, last_ids):
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
        it["score"] = total_score(it, last_ids)
    items.sort(key=lambda x: x["score"], reverse=True)
    save_current_ids(items)
    return items[:5], total

# ===== 카카오톡 전송 =====
def send_text_message(access_token, content):
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {access_token}"}
    template_object = {
        "object_type": "text",
        "text": content,
        "link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL},
        "button_title": "전체 공고 보기"
    }
    data = {"template_object": json.dumps(template_object, ensure_ascii=False)}
    r = requests.post(url, headers=headers, data=data)
    print("전송 결과:", r.json())

# ===== 메인 실행 =====
def main():
    access_token = refresh_access_token()
    top5, total = rank_top5()

    today = datetime.now(KST).strftime("%Y-%m-%d")
    text = [f"📅 {today} 기준 AI 추천 TOP 5 채용공고",
            f"총 {total}개 중 선별된 상위 공고입니다.\n"]

    for idx, item in enumerate(top5, start=1):
        text.append(f"{idx}위 ({item['score']}점) | {item['company']} | {item['location']}")
        text.append(f"🔗 {item['url']}\n")

    text.append(f"👇 전체 보기: {PAGES_URL}")
    final_message = "\n".join(text)

    send_text_message(access_token, final_message)

if __name__ == "__main__":
    main()
