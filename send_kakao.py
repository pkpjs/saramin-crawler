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
STATE_PATH    = "docs/last_rec_ids.json"   # 신규 감지용 저장 파일
SARAMIN_BASE  = "https://www.saramin.co.kr"

# 지역 선호 (옵션). 예: "대전,대구,수도권"
PREFERRED_REGIONS = [s.strip() for s in os.getenv("PREFERRED_REGIONS","").split(",") if s.strip()]

# 우선순위: D(마감) > C(신규) > E(지역) > B(기업) > A(연봉)
DEADLINE_IMMINENT_3D = 50
DEADLINE_IMMINENT_7D = 40
DEADLINE_NONE        = 10
FRESH_NEW            = 30
FRESH_OLD            = -10
REGION_HIT           = 20   # 선호지역 있을 때만 적용
FIRM_BIG             = 15
FIRM_MID             = 10
SALARY_GOOD          = 5

BIG_FIRM_HINTS = ["대기업", "공기업", "공사", "공단", "그룹",
                  "삼성","LG","현대","롯데","한화","SK","카카오","네이버","KT","포스코"]
MID_FIRM_HINTS = ["중견","강소","우량"]

def refresh_access_token() -> str:
    url = "https://kauth.kakao.com/oauth/token"
    data = {"grant_type": "refresh_token", "client_id": REST_API_KEY, "refresh_token": REFRESH_TOKEN}
    r = requests.post(url, data=data, timeout=20); r.raise_for_status()
    js = r.json()
    if "access_token" not in js:
        raise RuntimeError(f"토큰 갱신 실패: {js}")
    return js["access_token"]

def load_html_text() -> str:
    try:
        with open(HTML_PATH, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except FileNotFoundError:
        r = requests.get(PAGES_URL, timeout=20); r.raise_for_status()
        return r.text

def parse_deadline(text: str):
    if not text: return None
    t = text.strip()
    if any(k in t for k in ["상시","수시","채용시"]): return None
    m = re.search(r'(\d{1,2})\s*[./-]\s*(\d{1,2})', t) or re.search(r'(\d{1,2})\s*월\s*(\d{1,2})\s*일', t)
    if not m: return None
    month, day = int(m.group(1)), int(m.group(2))
    now = datetime.now(KST); year = now.year
    d = datetime(year, month, day, tzinfo=KST)
    if d < now - timedelta(days=180): d = datetime(year+1, month, day, tzinfo=KST)
    return d

def days_to_deadline(d):
    if not d: return None
    return (d.date() - datetime.now(KST).date()).days

def extract_items():
    """
    표 컬럼 가정:
    링크/제목 | 회사 | 위치 | 경력 | 학력 | 마감일 | 바로가기 | (연봉/급여 있으면 가산)
    """
    html = load_html_text()
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if not table: return [], 0

    headers = [th.get_text(strip=True) for th in table.select("thead tr th")]
    if not headers:
        first = table.find("tr")
        if first: headers = [th.get_text(strip=True) for th in first.find_all(["th","td"])]

    rows = table.select("tbody tr") or table.find_all("tr")[1:]
    total = len(rows)

    def idx(*names):
        for n in names:
            if n in headers: return headers.index(n)
        return None

    i_link   = idx("링크","제목","title")
    i_company= idx("회사","company")
    i_loc    = idx("위치","location")
    i_job    = idx("직무","job")
    i_dead   = idx("마감일","마감","deadline")
    i_direct = idx("바로가기")
    i_salary = idx("연봉","급여","salary")

    items = []
    for tr in rows:
        tds = tr.find_all("td")
        if not tds: continue

        title, url = "", ""
        if i_link is not None and i_link < len(tds):
            a = tds[i_link].find("a", href=True)
            if a:
                title = a.get_text(strip=True)
                href  = a["href"].strip()
                url   = href if href.startswith("http") else urljoin(SARAMIN_BASE, href)
            else:
                title = tds[i_link].get_text(strip=True)

        if not url and i_direct is not None and i_direct < len(tds):
            a2 = tds[i_direct].find("a", href=True)
            if a2:
                href2 = a2["href"].strip()
                url = href2 if href2.startswith("http") else urljoin(SARAMIN_BASE, href2)

        company = tds[i_company].get_text(strip=True) if i_company is not None and i_company < len(tds) else ""
        loc     = tds[i_loc].get_text(strip=True)     if i_loc     is not None and i_loc     < len(tds) else ""
        job     = tds[i_job].get_text(strip=True)     if i_job     is not None and i_job     < len(tds) else ""
        deadraw = tds[i_dead].get_text(strip=True)    if i_dead    is not None and i_dead    < len(tds) else ""
        salary  = tds[i_salary].get_text(strip=True)  if i_salary  is not None and i_salary  < len(tds) else ""

        # rec_idx 추출 (신규성 판정용)
        rec_idx = None
        if url:
            m = re.search(r"rec_idx=(\d+)", url)
            if m: rec_idx = m.group(1)

        items.append({
            "title": title or "(제목 없음)",
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

def load_last_rec_ids():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()
    except Exception:
        return set()

def save_current_rec_ids(items):
    recs = [x["rec_idx"] for x in items if x.get("rec_idx")]
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(recs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[WARN] rec_ids 저장 실패:", e)

def firm_score(name: str):
    n = (name or "").lower()
    big = any(k.lower() in n for k in BIG_FIRM_HINTS)
    mid = any(k.lower() in n for k in MID_FIRM_HINTS)
    if big: return FIRM_BIG
    if mid: return FIRM_MID
    return 0

def salary_score(text: str):
    if not text: return 0
    if "협의" in text: return 0
    nums = [int(x) for x in re.findall(r'\d{3,4}', text)]
    if not nums: return 0
    # 3500 이상 언급되면 소폭 가산
    return SALARY_GOOD if max(nums) >= 3500 else 0

def region_score(loc: str):
    if not PREFERRED_REGIONS: return 0
    l = (loc or "").lower()
    return REGION_HIT if any(p.lower() in l for p in PREFERRED_REGIONS) else 0

def deadline_score(deadline):
    d = days_to_deadline(deadline)
    if d is None: return DEADLINE_NONE
    if d <= 3:    return DEADLINE_IMMINENT_3D
    if d <= 7:    return DEADLINE_IMMINENT_7D
    return max(0, 30 - min(d, 30))  # 멀수록 감소(최대 30→0)

def freshness_score(item, last_ids: set):
    rec = item.get("rec_idx")
    if rec and rec not in last_ids: return FRESH_NEW
    return FRESH_OLD

def score_item(item, last_ids: set):
    s = 0
    s += deadline_score(item["deadline"])                  # D
    s += freshness_score(item, last_ids)                   # C
    s += region_score(item["location"])                    # E (없으면 0)
    s += firm_score(item["company"])                       # B
    s += salary_score(item["salary"])                      # A
    return s

def rank_top10(items):
    last_ids = load_last_rec_ids()
    for it in items:
        it["score"] = score_item(it, last_ids)
    items.sort(key=lambda x: x["score"], reverse=True)
    top10 = items[:10]
    save_current_rec_ids(items)  # 이번 회차 기록
    return top10

# ===== 전송 =====
def send_feed(access_token, rank, item):
    # 본문: 회사 / 위치 | 직무 / 마감
    parts = []
    if item["company"]: parts.append(item["company"])
    line2 = " | ".join([t for t in [item["location"], item["job"]] if t])
    if line2: parts.append(line2)
    if item["deadline_text"]: parts.append(f"마감: {item['deadline_text']}")
    desc = "\n".join(parts)

    template_object = {
        "object_type": "feed",
        "content": {
            "title": f"{rank}위 ({item['score']}점) | {item['title']}",
            "description": desc,
            "link": {"web_url": item["url"], "mobile_web_url": item["url"]},
        },
        "buttons": [
            {"title": "공고 바로가기 🔗", "link": {"web_url": item["url"], "mobile_web_url": item["url"]}}
        ]
    }
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {access_token}"}
    data = {"template_object": json.dumps(template_object, ensure_ascii=False)}
    r = requests.post(url, headers=headers, data=data, timeout=20)
    js = r.json()
    if js.get("result_code") != 0:
        raise RuntimeError(f"{rank}위 전송 실패: {js}")

def send_header(access_token, total):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    text = f"📅 {today} AI 추천 TOP 10 (마감 임박 > 신규 > 지역 > 기업 > 연봉)\n원본 {total}건에서 선별/점수화했습니다."
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {access_token}"}
    data = {"template_object": json.dumps({
        "object_type": "text",
        "text": text,
        "link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL},
        "button_title": "전체 공고 보기"
    }, ensure_ascii=False)}
    requests.post(url, headers=headers, data=data, timeout=20)

def send_tail(access_token):
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {access_token}"}
    data = {"template_object": json.dumps({
        "object_type": "feed",
        "content": {
            "title": "전체 채용 공고 한 번에 보기",
            "description": "GitHub Pages에서 최신 전체 목록 확인",
            "link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL},
        },
        "buttons": [{"title": "전체보기 🔗", "link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL}}]
    }, ensure_ascii=False)}
    requests.post(url, headers=headers, data=data, timeout=20)

def main():
    access_token = refresh_access_token()
    items_all, total = extract_items()
    if not items_all:
        print("❌ 데이터 없음"); return
    top10 = rank_top10(items_all)

    send_header(access_token, total)
    for i, it in enumerate(top10, start=1):
        send_feed(access_token, i, it)
    send_tail(access_token)
    print(f"✅ 전송 완료: {len(top10)}개")

if __name__ == "__main__":
    main()