import os, re, json, requests
from bs4 import BeautifulSoup

REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
REDIRECT_URI  = os.getenv("KAKAO_REDIRECT_URI")
REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN")
ACCESS_TOKEN  = os.getenv("KAKAO_ACCESS_TOKEN")  # 없으면 refresh로 발급
PAGES_URL     = os.getenv("PAGES_URL", "https://pkpjs.github.io/test/saramin_results_latest.html")
TEMPLATE_ID   = 125299
HTML_PATH     = "docs/saramin_results_latest.html"

SARAMIN_PAT = re.compile(r"saramin\.co\.kr", re.I)

def refresh_access_token():
    url = "https://kauth.kakao.com/oauth/token"
    data = {"grant_type": "refresh_token", "client_id": REST_API_KEY, "refresh_token": REFRESH_TOKEN}
    res = requests.post(url, data=data, timeout=15)
    res.raise_for_status()
    j = res.json()
    if "access_token" not in j:
        raise RuntimeError(f"[ERR] 토큰 갱신 실패: {j}")
    return j["access_token"]

def load_html(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def smart_extract_top10(html, limit=10):
    """
    HTML 구조를 모른다는 전제에서 회사/위치/링크를 최대한 뽑는 휴리스틱 파서.
    - 우선순위: 테이블 → 카드형 div → 모든 a[href] 중 saramin 링크
    """
    soup = BeautifulSoup(html, "lxml")

    # 1) 테이블 기반 추출 시도
    rows = []
    for table in soup.find_all(["table"]):
        trs = table.find_all("tr")
        for tr in trs:
            tds = tr.find_all(["td", "th"])
            if len(tds) < 2:
                continue
            txts = [t.get_text(" ", strip=True) for t in tds]
            a = tr.find("a", href=True)
            href = a["href"] if a else ""
            if href and not href.startswith("http"):
                # 상대경로면 그대로 사용(사람인은 절대URL일 확률 큼)
                pass
            if href and not SARAMIN_PAT.search(href):
                # 사람인 링크가 아니면 스킵(노이즈 제거)
                href = ""
            title = a.get_text(" ", strip=True) if a and a.get_text(strip=True) else txts[0]
            # 위치/직무 추정
            desc_candidates = [t for t in txts[1:] if len(t) >= 2]
            desc = " · ".join(desc_candidates[:2]) if desc_candidates else ""
            if title:
                rows.append({"title": title, "desc": desc, "url": href})

    if len(rows) >= 3:  # 충분히 뽑혔다고 판단
        uniq = []
        seen = set()
        for r in rows:
            key = (r["title"], r["url"])
            if key not in seen:
                uniq.append(r)
                seen.add(key)
        return uniq[:limit]

    # 2) 카드형 div 추정: 사람인 상세 링크가 달린 a 태그들 수집
    items = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not SARAMIN_PAT.search(href):
            continue
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 2:
            continue
        # 주변 텍스트로 위치/직무 힌트 추출
        box = a.find_parent()
        desc = ""
        if box:
            txt = box.get_text(" ", strip=True)
            # 제목을 제외하고 남는 문장에서 위치/직무 비슷한 라인 추정
            txt = re.sub(re.escape(title), " ", txt)
            # 너무 길면 자름
            desc = re.sub(r"\s+", " ", txt).strip()
            if len(desc) > 80:
                desc = desc[:80] + "…"
        items.append({"title": title, "desc": desc, "url": href})

    if len(items) >= 3:
        uniq = []
        seen = set()
        for r in items:
            key = (r["title"], r["url"])
            if key not in seen:
                uniq.append(r)
                seen.add(key)
        return uniq[:limit]

    # 3) 최후: 모든 사람인 링크에서 텍스트로 뽑기
    fallback = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not SARAMIN_PAT.search(href):
            continue
        title = a.get_text(" ", strip=True) or "채용공고"
        fallback.append({"title": title, "desc": "", "url": href})
    # 중복 제거
    seen = set(); uniq = []
    for r in fallback:
        key = (r["title"], r["url"])
        if key not in seen:
            uniq.append(r); seen.add(key)
    return uniq[:limit]

def build_template_args(top10):
    """
    커머스 슬라이드용: item_title_1..10 / item_desc_1..10 / item_url_1..10 채우기
    부족하면 공백으로 채움(템플릿이 10장 고정인 경우 안전).
    """
    args = {}
    for idx in range(10):
        i = idx + 1
        if idx < len(top10):
            it = top10[idx]
            title = (it.get("title") or "").strip()
            desc  = (it.get("desc") or "").strip()
            url   = (it.get("url") or "").strip()
            # 설명이 비면 제목에서 중복 제거한 간단 설명 생성
            if not desc:
                desc = "채용 공고"
            args[f"item_title_{i}"] = title[:60] if title else f"채용공고 #{i}"
            args[f"item_desc_{i}"]  = desc[:80]
            args[f"item_url_{i}"]   = url or PAGES_URL
        else:
            # 남는 슬롯은 비워 둠(템플릿에서 빈 카드 숨김 처리되어 있으면 자연스러움)
            args[f"item_title_{i}"] = ""
            args[f"item_desc_{i}"]  = ""
            args[f"item_url_{i}"]   = PAGES_URL
    return args

def send_message(args, access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    data = {
        "template_id": TEMPLATE_ID,
        "template_args": json.dumps(args, ensure_ascii=False),
    }
    res = requests.post("https://kapi.kakao.com/v2/api/talk/memo/send",
                        headers=headers, data=data, timeout=15)
    try:
        j = res.json()
    except Exception:
        j = {"status_code": res.status_code, "text": res.text}
    print("KAKAO RESP:", j)
    if not (isinstance(j, dict) and j.get("result_code") == 0):
        raise RuntimeError(f"[ERR] 메시지 전송 실패: {j}")

def main():
    global ACCESS_TOKEN
    if not ACCESS_TOKEN:
        ACCESS_TOKEN = refresh_access_token()

    html = load_html(HTML_PATH)
    top10 = smart_extract_top10(html, limit=10)
    if not top10:
        # 그래도 빈 값으로 카드 전송(버튼은 전체보기)
        top10 = [{"title": "채용공고", "desc": "", "url": PAGES_URL}]

    args = build_template_args(top10)

    try:
        send_message(args, ACCESS_TOKEN)
        print("✅ 메시지 전송 성공")
        return
    except Exception as e:
        print("[WARN] 1차 전송 실패, 토큰 갱신 후 재시도:", e)

    # 토큰 만료 대비 재시도
    ACCESS_TOKEN = refresh_access_token()
    send_message(args, ACCESS_TOKEN)
    print("✅ 메시지 전송 성공(재시도)")

if __name__ == "__main__":
    main()
