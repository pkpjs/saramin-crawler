# send_kakao.py
# - Kakao Refresh Token으로 Access Token 자동 갱신
# - GitHub Pages의 최신 결과 HTML에서 상위 3개 공고 추출
# - 카카오톡 "나에게" 메시지 전송 (default text template + 버튼)
#
# 필요 ENV (GitHub Secrets로 주입):
#   KAKAO_REST_API_KEY
#   KAKAO_REDIRECT_URI           (예: https://localhost)
#   KAKAO_REFRESH_TOKEN
#   # 선택: KAKAO_ACCESS_TOKEN  (없어도 자동 갱신됨)
#   PAGES_URL                    (예: https://pkpjs.github.io/test/saramin_results_latest.html)

import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

REST_API_KEY    = os.environ["KAKAO_REST_API_KEY"]
REDIRECT_URI    = os.environ.get("KAKAO_REDIRECT_URI", "https://localhost")
REFRESH_TOKEN   = os.environ["KAKAO_REFRESH_TOKEN"]
PAGES_URL       = os.environ.get("PAGES_URL", "https://pkpjs.github.io/test/saramin_results_latest.html")

def refresh_access_token() -> str:
    """Refresh Token으로 Access Token 갱신"""
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": REST_API_KEY,
        "refresh_token": REFRESH_TOKEN,
        # redirect_uri는 refresh_token 플로우에서는 필수 아님 (문서상)
    }
    resp = requests.post(url, data=data, timeout=20)
    resp.raise_for_status()
    js = resp.json()
    if "access_token" not in js:
        raise RuntimeError(f"Failed to refresh access token: {js}")
    access_token = js["access_token"]
    return access_token

def parse_top3_from_pages(pages_url: str):
    """
    GitHub Pages HTML 표에서 상위 3개 공고 추출.
    기대 컬럼: 제목/회사/위치/...(DataFrame.to_html 기본 구조)
    """
    res = requests.get(pages_url, timeout=20)
    res.raise_for_status()

    soup = BeautifulSoup(res.text, "lxml")

    # pandas DataFrame.to_html 형태: <table> 안에 <thead><tr><th>...</th> + <tbody><tr><td>...</td>
    table = soup.find("table")
    if not table:
        # fallback: 테이블이 없다면 링크만 리턴
        return [], 0

    # 헤더 매핑
    headers = [th.get_text(strip=True) for th in table.select("thead tr th")]
    # pandas가 <thead> 없이 만드는 경우도 대비
    if not headers:
        first_row = table.find("tr")
        if first_row:
            headers = [th.get_text(strip=True) for th in first_row.find_all(["th","td"])]

    # 행들
    rows = table.select("tbody tr")
    if not rows:
        # 일부 환경에서 <tbody> 생략 → 직접 tr 수집
        rows = table.find_all("tr")[1:]  # 첫 tr은 헤더로 가정

    total = len(rows)

    # 컬럼 인덱스 찾기
    def idx(col_name):
        try:
            return headers.index(col_name)
        except Exception:
            return None

    idx_title   = idx("제목") or idx("title")
    idx_company = idx("회사") or idx("company")
    idx_loc     = idx("위치") or idx("location")

    top_items = []
    for tr in rows[:3]:
        tds = tr.find_all("td")
        if not tds:
            continue
        title = tds[idx_title].get_text(strip=True) if (idx_title is not None and idx_title < len(tds)) else ""
        comp  = tds[idx_company].get_text(strip=True) if (idx_company is not None and idx_company < len(tds)) else ""
        loc   = tds[idx_loc].get_text(strip=True) if (idx_loc is not None and idx_loc < len(tds)) else ""
        if title or comp:
            # 너무 긴 제목은 잘라줌
            if len(title) > 50:
                title = title[:47] + "..."
            top_items.append((title, comp, loc))

    return top_items, total

def build_text_message(date_str: str, total: int, top3: list, pages_url: str) -> str:
    lines = []
    lines.append(f"[{date_str} 채용공고 요약]")
    lines.append(f"총 {total}개 공고 업데이트됨")
    lines.append("")
    if top3:
        lines.append("🔥 TOP 3 공고")
        for i, (title, comp, loc) in enumerate(top3, start=1):
            loc_txt = f" ({loc})" if loc else ""
            lines.append(f"{i}. {title} - {comp}{loc_txt}")
        lines.append("")
    lines.append("👇 전체 공고 보기 버튼을 눌러 확인하세요.")
    return "\n".join(lines)

def send_kakao_text(access_token: str, text: str, link_url: str, button_title: str = "전체 공고 보기"):
    """
    카카오톡 '나에게' 기본 텍스트 템플릿 전송
    API: POST https://kapi.kakao.com/v2/api/talk/memo/default/send
    template_object: {
      "object_type":"text",
      "text":"내용",
      "link":{"web_url":"...","mobile_web_url":"..."},
      "button_title":"버튼명"
    }
    """
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    template_object = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": link_url, "mobile_web_url": link_url},
        "button_title": button_title
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"
    }
    data = {
        "template_object": json.dumps(template_object, ensure_ascii=False)
    }
    resp = requests.post(url, headers=headers, data=data, timeout=20)
    # 카카오 응답은 200이라도 {"result_code":0} 확인 필요
    if resp.status_code != 200:
        raise RuntimeError(f"Kakao send failed: {resp.status_code} {resp.text}")
    js = resp.json()
    if js.get("result_code") != 0:
        raise RuntimeError(f"Kakao send non-zero result_code: {js}")
    return js

def main():
    # 1) Access Token 갱신
    try:
        access_token = refresh_access_token()
    except Exception as e:
        raise SystemExit(f"[ERR] Access Token refresh 실패: {e}")

    # 2) 상위 3개 공고 파싱
    try:
        top3, total = parse_top3_from_pages(PAGES_URL)
    except Exception as e:
        # 페이지가 깨져도 최소한 버튼만 보낼 수 있게 total 0으로
        top3, total = [], 0
        print(f"[WARN] 페이지 파싱 실패: {e}")

    # 3) 메시지 본문 구성 (KST 기준 날짜)
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    text = build_text_message(today_str, total, top3, PAGES_URL)

    # 4) 카카오톡 전송
    try:
        result = send_kakao_text(access_token, text, PAGES_URL, button_title="전체 공고 보기")
        print("[OK] Kakao message sent:", result)
    except Exception as e:
        raise SystemExit(f"[ERR] Kakao send 실패: {e}")

if __name__ == "__main__":
    main()
