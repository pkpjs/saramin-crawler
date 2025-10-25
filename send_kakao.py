import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# ✅ GitHub Secrets or 환경 변수
REST_API_KEY  = os.getenv("KAKAO_REST_API_KEY")
REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN")
PAGES_URL     = os.getenv("PAGES_URL", "https://pkpjs.github.io/test/saramin_results_latest.html")
HTML_PATH     = "docs/saramin_results_latest.html"

# ✅ Access Token 갱신
def refresh_access_token() -> str:
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": REST_API_KEY,
        "refresh_token": REFRESH_TOKEN,
    }
    resp = requests.post(url, data=data, timeout=20)
    resp.raise_for_status()
    js = resp.json()
    if "access_token" not in js:
        raise RuntimeError(f"Access Token refresh 실패: {js}")
    print("🔄 Access Token 자동 갱신 완료")
    return js["access_token"]

# ✅ HTML 파일에서 TOP10 파싱
def parse_top10_from_html():
    try:
        with open(HTML_PATH, "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()
    except FileNotFoundError:
        print("⚠ HTML 파일을 로컬에서 찾지 못함. GitHub Pages에서 직접 파싱 시도")
        html = requests.get(PAGES_URL, timeout=20).text

    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if not table:
        return [], 0

    headers = [th.get_text(strip=True) for th in table.select("thead tr th")]
    if not headers:
        first_row = table.find("tr")
        headers = [th.get_text(strip=True) for th in first_row.find_all(["th","td"])]

    rows = table.select("tbody tr")
    if not rows:
        rows = table.find_all("tr")[1:]

    total = len(rows)

    def get_idx(name):
        try:
            return headers.index(name)
        except:
            return None

    idx_title   = get_idx("제목") or get_idx("title")
    idx_company = get_idx("회사") or get_idx("company")
    idx_loc     = get_idx("위치") or get_idx("location")
    idx_job     = get_idx("직무") or get_idx("job")

    top_items = []
    for tr in rows[:10]:
        tds = tr.find_all("td")
        if not tds:
            continue
        title = tds[idx_title].get_text(strip=True) if idx_title is not None else ""
        comp  = tds[idx_company].get_text(strip=True) if idx_company is not None else ""
        loc   = tds[idx_loc].get_text(strip=True) if idx_loc is not None else ""
        job   = tds[idx_job].get_text(strip=True) if idx_job is not None else ""

        desc = f"{loc} | {job}" if job else loc
        top_items.append((title, comp, desc))

    return top_items, total

# ✅ 카드 스타일 텍스트 구성
def build_card_message(date_str, total, top_items):
    lines = []
    lines.append(f"📌 [{date_str} 채용공고 TOP {len(top_items)} 요약]")
    lines.append(f"총 {total}개 공고가 업데이트되었습니다.\n")

    for i, (title, comp, desc) in enumerate(top_items, start=1):
        lines.append("┌───────────────────────")
        lines.append(f"│ {i}위  {title}")
        if comp:
            lines.append(f"│ 🏢 {comp}")
        if desc:
            lines.append(f"│ 📍 {desc}")
        lines.append("└───────────────────────")

    lines.append("\n👇 아래 버튼을 눌러 전체 공고를 확인하세요.")
    return "\n".join(lines)

# ✅ 카카오톡 전송 (템플릿 없이 기본 API)
def send_kakao_text(access_token, text, link_url):
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    template_object = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": link_url, "mobile_web_url": link_url},
        "button_title": "전체 공고 보기"
    }
    headers = {"Authorization": f"Bearer {access_token}"}
    data = {"template_object": json.dumps(template_object, ensure_ascii=False)}

    resp = requests.post(url, headers=headers, data=data, timeout=20)
    result = resp.json()
    print("📩 카카오 응답:", result)

    if result.get("result_code") == 0:
        print("✅ 메시지 전송 성공!")
    else:
        raise RuntimeError(f"메시지 전송 실패: {result}")

# ✅ 메인 실행
def main():
    access_token = refresh_access_token()
    top_items, total = parse_top10_from_html()
    today_str = datetime.now(KST).strftime("%Y-%m-%d")

    if not top_items:
        print("⚠ TOP10 데이터를 찾지 못함. 전체 링크만 보냅니다.")
        text = f"[{today_str} 채용공고 요약]\n데이터를 불러올 수 없습니다.\n👇 전체보기: {PAGES_URL}"
    else:
        text = build_card_message(today_str, total, top_items)

    send_kakao_text(access_token, text, PAGES_URL)

if __name__ == "__main__":
    main()
