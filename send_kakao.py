# -*- coding: utf-8 -*-
import os, re, json, requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# ===== 기본 설정 =====
KST = timezone(timedelta(hours=9))
REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN")
PAGES_URL = os.getenv("PAGES_URL", "https://pkpjs.github.io/test/saramin_results_latest.html")
HTML_PATH = "docs/saramin_results_latest.html"
STATE_PATH = "docs/last_rec_ids.json"
SARAMIN_BASE = "https://www.saramin.co.kr"

# ===== 점수 가중치 =====
DEADLINE_IMMINENT_3D = 50
DEADLINE_IMMINENT_7D = 40
DEADLINE_NONE = 10
FRESH_NEW = 30
FRESH_OLD = -10
FIRM_BIG = 15
FIRM_MID = 10
SALARY_GOOD = 5

BIG_FIRM_HINTS = ["대기업","공기업","공사","공단","그룹","삼성","LG","현대","롯데","한화","SK","카카오","네이버","KT","포스코"]
MID_FIRM_HINTS = ["중견","강소","우량"]

# ===== Kakao 토큰 갱신 =====
def refresh_access_token() -> str:
    url = "https://kauth.kakao.com/oauth/token"
    data = {"grant_type": "refresh_token", "client_id": REST_API_KEY, "refresh_token": REFRESH_TOKEN}
    r = requests.post(url, data=data, timeout=20)
    r.raise_for_status()
    js = r.json()
    if "access_token" not in js:
        raise RuntimeError(f"토큰 갱신 실패: {js}")
    return js["access_token"]

# ===== HTML 로드 및 정제 =====
def load_html_text() -> str:
    try:
        with open(HTML_PATH, "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()
    except FileNotFoundError:
        r = requests.get(PAGES_URL, timeout=20)
        r.raise_for_status()
        html = r.text

    # ✅ <style>, <script> 제거 (파싱 방해 방지)
    cleaned = re.sub(r"<style.*?>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<script.*?>.*?</script>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned

# ===== 마감일 파싱 =====
def parse_deadline(text: str):
    if not text:
        return None
    t = text.strip()
    if any(k in t for k in ["상시","수시","채용시","상시채용","수시채용"]):
        return None
    m = re.search(r'(\d{1,2})\s*[./-]\s*(\d{1,2})', t) or re.search(r'(\d{1,2})\s*월\s*(\d{1,2})\s*일', t)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    now = datetime.now(KST)
    year = now.year
    d = datetime(year, month, day, tzinfo=KST)
    if d < now - timedelta(days=180):
        d = datetime(year+1, month, day, tzinfo=KST)
    return d

def days_to_deadline(d):
    if not d: return None
    return (d.date() - datetime.now(KST).date()).days

def format_deadline_display(deadline_dt, raw_text: str) -> str:
    t = (raw_text or "").strip()
    low = t.replace(" ", "").lower()
    if any(k in low for k in ["오늘마감","today"]): return "오늘마감"
    if any(k in low for k in ["내일마감","tomorrow"]): return "내일마감"
    if any(k in t for k in ["상시","수시","채용시","상시채용","수시채용"]): return "상시채용"
    if deadline_dt:
        dday = days_to_deadline(deadline_dt)
        mmdd = deadline_dt.strftime("%m/%d")
        if dday is not None:
            if dday < 0:  return f"마감 {mmdd} (D+{abs(dday)})"
            if dday == 0: return f"마감 {mmdd} (D-0)"
            return f"마감 {mmdd} (D-{dday})"
        return f"마감 {mmdd}"
    return t or ""

# ===== 공고 추출 =====
def extract_items():
    html = load_html_text()
    soup = BeautifulSoup(html, "lxml")

    table = soup.find("table")
    if not table:
        # ✅ 백업 로직: <h2> 이후 <table> 강제 탐색
        for tag in soup.find_all("table"):
            table = tag
            break
        if not table:
            print("❌ 테이블을 찾지 못했습니다. HTML 구조를 확인하세요.")
            return [], 0

    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    rows = table.find_all("tr")[1:]
    total = len(rows)

    def idx(*names):
        for n in names:
            if n in headers:
                return headers.index(n)
        return None

    i_title   = idx("제목")
    i_company = idx("회사","company")
    i_loc     = idx("위치","location")
    i_job     = idx("직무","job")
    i_dead    = idx("마감일","마감","deadline")
    i_salary  = idx("연봉","급여","salary")
    i_link_col = idx("링크")

    items = []
    for tr in rows:
        tds = tr.find_all("td")
        if not tds: continue

        title, url, rec_idx = "", "", None

        if i_title is not None and i_title < len(tds):
            title = tds[i_title].get_text(strip=True)

        if i_link_col is not None and i_link_col < len(tds):
            a = tds[i_link_col].find("a", href=True)
            if a:
                full_url = a["href"].strip()
                m = re.search(r"rec_idx=(\d+)", full_url)
                if m:
                    rec_idx = m.group(1)
                    url = f"https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx={rec_idx}"
                else:
                    url = full_url

        company = tds[i_company].get_text(strip=True) if i_company is not None and i_company < len(tds) else ""
        loc     = tds[i_loc].get_text(strip=True) if i_loc is not None and i_loc < len(tds) else ""
        job     = tds[i_job].get_text(strip=True) if i_job is not None and i_job < len(tds) else "(직무정보없음)"
        deadraw = tds[i_dead].get_text(strip=True) if i_dead is not None and i_dead < len(tds) else ""
        salary  = tds[i_salary].get_text(strip=True) if i_salary is not None and i_salary < len(tds) else ""

        deadline_dt = parse_deadline(deadraw)
        deadline_disp = format_deadline_display(deadline_dt, deadraw)

        items.append({
            "title": title or "(제목 없음)",
            "company": company,
            "location": loc,
            "job": job,
            "deadline_text": deadraw,
            "deadline": deadline_dt,
            "deadline_disp": deadline_disp,
            "salary": salary,
            "url": url or PAGES_URL,
            "rec_idx": rec_idx
        })
    return items, total

# ===== rec_idx 관리 =====
def load_last_rec_ids():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_current_rec_ids(items):
    recs = [x["rec_idx"] for x in items if x.get("rec_idx")]
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(recs, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ===== 점수 계산 =====
def deadline_score(deadline):
    d = days_to_deadline(deadline)
    if d is None: return DEADLINE_NONE
    if d <= 3:    return DEADLINE_IMMINENT_3D
    if d <= 7:    return DEADLINE_IMMINENT_7D
    return max(0, 30 - min(d, 30))

def freshness_score(item, last_ids: set):
    rec = item.get("rec_idx")
    return FRESH_NEW if rec and rec not in last_ids else FRESH_OLD

def firm_score(name: str):
    n = (name or "").lower()
    if any(k.lower() in n for k in BIG_FIRM_HINTS): return FIRM_BIG
    if any(k.lower() in n for k in MID_FIRM_HINTS): return FIRM_MID
    return 0

def salary_score(text: str):
    if not text: return 0
    if "협의" in text: return 0
    nums = [int(x) for x in re.findall(r'\d{3,4}', text)]
    if nums and max(nums) >= 3500:
        return SALARY_GOOD
    return 0

def score_item(item, last_ids: set):
    return (
        deadline_score(item["deadline"]) +
        freshness_score(item, last_ids) +
        firm_score(item["company"]) +
        salary_score(item["salary"])
    )

def rank_top(items, k=5):
    last_ids = load_last_rec_ids()
    for it in items:
        it["score"] = score_item(it, last_ids)
    items.sort(key=lambda x: x["score"], reverse=True)
    topk = items[:k]
    save_current_rec_ids(items)
    return topk

# ===== 카카오톡 전송 =====
def send_text(access_token: str, text: str):
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {access_token}"}
    chunks = []
    buf = []
    cur_len = 0
    for line in text.splitlines():
        add = len(line) + 1
        if cur_len + add > 1000:
            chunks.append("\n".join(buf))
            buf, cur_len = [], 0
        buf.append(line)
        cur_len += add
    if buf:
        chunks.append("\n".join(buf))

    for i, chunk in enumerate(chunks, start=1):
        suffix = f"\n\n(#{i}/{len(chunks)})" if len(chunks) > 1 else ""
        template_object = {
            "object_type": "text",
            "text": chunk + suffix,
            "link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL},
            "button_title": "전체 공고 보기"
        }
        data = {"template_object": json.dumps(template_object, ensure_ascii=False)}
        r = requests.post(url, headers=headers, data=data, timeout=20)
        try:
            print("전송 결과:", r.json())
        except Exception:
            print("전송 결과:", r.text)

# ===== 메인 =====
def main():
    access_token = refresh_access_token()
    items_all, total = extract_items()
    if not items_all:
        print("❌ 데이터 없음")
        return

    top5 = rank_top(items_all, k=5)

    today = datetime.now(KST).strftime("%Y-%m-%d")
    lines = []
    lines.append(f"📅 {today} 기준 AI 추천 TOP 5 채용공고")
    lines.append(f"총 {total}개 중 선별된 상위 공고입니다.\n")

    for i, it in enumerate(top5, start=1):
        title_line = f"{i}위 ({it['score']}점) | {it['company']} / {it['job']} | {it['location']} | {it['deadline_disp']}"
        lines.append(title_line)
        real_link = it.get('url') or f"https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx={it.get('rec_idx','')}"
        lines.append(f"🔗 {real_link}\n")

    lines.append(f"👇 전체 공고 보기:\n{PAGES_URL}")
    final_message = "\n".join(lines)
    send_text(access_token, final_message)
    print(f"✅ 전송 완료: {len(top5)}개 항목")

if __name__ == "__main__":
    main()
