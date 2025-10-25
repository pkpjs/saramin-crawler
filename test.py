import math
import time
import os
import re
import requests
from bs4 import BeautifulSoup, NavigableString
import pandas as pd
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Tuple, Optional, List


# -------------------------------
# Saramin Crawler (검색 + 상세 파싱: A모드=원문 정리 + 요약)
# -------------------------------
class SaraminCrawler:
    def __init__(self):
        self.api_url = "https://www.saramin.co.kr/zf_user/search/get-recruit-list"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.saramin.co.kr/zf_user/search",
            "X-Requested-With": "XMLHttpRequest",
        }
        # 🔎 검색 조건(요청 조건)
        self.params = {
            "searchType": "search",
            "loc_mcd": "106000,104000,105000,107000,110000,111000",   # 부산/대구/대전/울산/경남/경북
            "cat_kewd": "83,84,85,90,104,108,111,112,114,116",       # 데이터엔지니어 외 10개
            "company_cd": "0,1,2,3,4,5,6,7,9,10",                    # 회사형태 전체
            "exp_cd": "1",                                           # 신입
            "exp_none": "y",                                         # 경력무관 포함
            "job_type": "1",                                         # 정규직
            "search_optional_item": "y",
            "search_done": "y",
            "panel_count": "y",
            "preview": "y",
            "recruitPage": 1,
            "recruitPageCount": 40,                                  # 페이지당 40개
            "recruitSort": "relation"                                # 관련도순
        }

        # 불필요 텍스트 제거 패턴(상세 본문 클린업)
        self.noise_patterns = [
            r"\b로그인\b", r"\b회원가입\b", r"기업서비스", r"사람인\s*비즈니스", r"사람인\s*고객센터",
            r"\bTOP\b", r"\b이전공고\b", r"\b다음공고\b", r"검색\s*폼", r"공지사항", r"이벤트",
            r"커리어\s*피드", r"사람인\s*스토어", r"채용정보\s*지역별", r"HOT100", r"헤드헌팅", r"큐레이션",
            r"파견대행", r"외국인\s*채용", r"중장년\s*채용", r"취업축하금", r"신입·인턴", r"채용달력",
            r"연봉정보", r"면접후기", r"기업큐레이션", r"이력서\s*양식", r"HR매거진",
            r"인적성검사", r"서류\s*작성\s*코칭", r"면접\s*코칭", r"자기\s*계발", r"자격증\s*준비",
            r"사람인\s*인공지능", r"맞춤\s*공고", r"Ai\s*매치", r"Ai\s*모의면접"
        ]
        self.noise_regex = re.compile("|".join(self.noise_patterns))

    # ---------- 유틸 ----------
    @staticmethod
    def _clean_ws(text: str) -> str:
        if not text:
            return ""
        t = text.replace("\r", "\n")
        t = re.sub(r"\u00A0", " ", t)  # non-breaking space
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\n{2,}", "\n", t)
        return t.strip()

    def _is_noise_line(self, line: str) -> bool:
        if not line:
            return True
        if len(line) < 2:
            return True
        if self.noise_regex.search(line):
            return True
        # 지나치게 메뉴성 나열
        if sum(1 for ch in line if ch == "·" or ch == "|") >= 3:
            return True
        return False

    def _dedup_lines_ordered(self, lines: List[str]) -> List[str]:
        seen = set()
        out = []
        for ln in lines:
            k = ln.strip()
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(k)
        return out

    # ---------- 검색결과 파싱 ----------
    def _parse_jobs_from_innerHTML(self, inner_html):
        soup = BeautifulSoup(inner_html, "html.parser")
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

                company_el = item.select_one("strong.corp_name a, strong.corp_name")
                company = company_el.get_text(strip=True) if company_el else ""

                cond_spans = item.select("div.job_condition span")
                location = cond_spans[0].get_text(strip=True) if len(cond_spans) > 0 else ""
                career   = cond_spans[1].get_text(strip=True) if len(cond_spans) > 1 else ""
                edu      = cond_spans[2].get_text(strip=True) if len(cond_spans) > 2 else ""

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
                    "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
            except Exception:
                continue
        return jobs

    def _fetch_page(self, page: int):
        params = dict(self.params)
        params["recruitPage"] = page
        resp = requests.get(self.api_url, params=params, headers=self.headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        inner_html = data.get("innerHTML", "")
        count_str = data.get("count", "0")
        try:
            total_count = int(str(count_str).replace(",", ""))
        except Exception:
            total_count = 0

        jobs = self._parse_jobs_from_innerHTML(inner_html) if inner_html else []
        return jobs, total_count

    def crawl_all(self, sleep_sec=0.6, page_limit=None) -> pd.DataFrame:
        print("🔎 수집 시작...")
        all_jobs = []
        first_page_jobs, total_count = self._fetch_page(1)
        if not first_page_jobs:
            print("⚠ 첫 페이지에서 공고를 찾지 못했습니다. (헤더/파라미터 확인 필요)")
            return pd.DataFrame()

        all_jobs.extend(first_page_jobs)

        page_count = math.ceil(total_count / int(self.params["recruitPageCount"])) if total_count else 1
        if page_limit is not None:
            page_count = min(page_count, page_limit)

        print(f"📊 총 {total_count}건 추정, {page_count}페이지 예정")

        for p in range(2, page_count + 1):
            print(f"📄 {p}/{page_count} 페이지 수집 중...")
            jobs, _ = self._fetch_page(p)
            if not jobs:
                print("⛔ 더 이상 공고가 없습니다.")
                break
            all_jobs.extend(jobs)
            time.sleep(sleep_sec)

        df = pd.DataFrame(all_jobs)
        if df.empty:
            print("⚠ 수집된 데이터가 없습니다.")
            return df

        # 중복 제거: rec_idx 우선, 없으면 link
        if "rec_idx" in df.columns:
            df["__dedup_key"] = df["rec_idx"].where(df["rec_idx"].astype(bool), other=df["link"])
            df.drop_duplicates(subset=["__dedup_key"], inplace=True)
            df.drop(columns=["__dedup_key"], inplace=True)
        else:
            df.drop_duplicates(subset=["link"], inplace=True)

        return df

    # ---------- 상세페이지 파싱 (A 모드: 원문+정제) ----------
    def _find_content_container(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """
        상세 본문이 들어있는 최상위 컨테이너를 최대한 정확히 선택
        """
        selectors = [
            "#content .wrap_jview",
            "#content .jv-cont",
            "#content .jv_detail",
            "#content .cont",
            "#recruit_info",
            ".wrap_jv_cont",
            ".jview .cont",
            "#content"
        ]
        for sel in selectors:
            node = soup.select_one(sel)
            if node and len(node.get_text(strip=True)) > 50:
                return node
        return soup  # fallback

    def _strip_noise_nodes(self, node: BeautifulSoup):
        """
        본문 외 잡영역 제거
        """
        for tag in node.find_all(["script", "style", "noscript", "iframe"]):
            tag.decompose()
        for tag in node.find_all(["header", "footer", "nav", "aside"]):
            tag.decompose()
        # 메뉴/광고성 섹션들 추정 클래스 제거
        junk_classes = [
            "gnb", "lnb", "breadcrumb", "sidebar", "banner", "ad", "advert", "footer",
            "login", "signup", "jv-relate", "sns", "share", "floating", "btn_top"
        ]
        for cls in junk_classes:
            for t in node.select(f".{cls}"):
                t.decompose()

    def _extract_label_value(self, soup: BeautifulSoup, labels: List[str]) -> Optional[str]:
        """
        라벨-값 구조 추출 (dt/dd, th/td, strong/label 등)
        """
        nodes = soup.find_all(string=re.compile("|".join([re.escape(x) for x in labels])))
        for node in nodes:
            parent = node.parent
            if not parent:
                continue
            # dt -> dd
            if parent.name == "dt":
                dd = parent.find_next_sibling("dd")
                if dd:
                    return self._clean_ws(dd.get_text(" ", strip=True))
            # th -> td
            if parent.name == "th":
                td = parent.find_next_sibling("td")
                if td:
                    return self._clean_ws(td.get_text(" ", strip=True))
            # strong/label 바로 다음 형제
            sib = parent.find_next_sibling()
            if sib and sib.name in ["dd", "td", "p", "div", "span"]:
                val = self._clean_ws(sib.get_text(" ", strip=True))
                if val:
                    return val
            # 같은 줄에서 콜론 등으로 이어진 케이스
            line = self._clean_ws(parent.get_text(" ", strip=True))
            for kw in labels:
                if kw in line:
                    after = line.split(kw, 1)[1].lstrip(": -—\t")
                    if after:
                        return self._clean_ws(after)
        return None

    def _extract_section_text(self, node: BeautifulSoup, title_patterns: List[str]) -> Optional[str]:
        """
        섹션(자격요건/복리후생 등)을 정제 텍스트로 추출
        - 타이틀과 가까운 DOM을 우선 추적
        - li/p/div/span 텍스트 취합
        - 노이즈 라인 제거, 중복 제거
        """
        regex = re.compile("|".join(title_patterns), re.IGNORECASE)
        hits = node.find_all(string=regex)
        candidates = []

        for hit in hits:
            box = hit
            # 상위로 2~3단계 올려 섹션 래퍼 추정
            for _ in range(3):
                if box and getattr(box, "parent", None):
                    box = box.parent
            if not box:
                continue

            texts = []
            for t in box.find_all(["li", "p", "dd", "td", "div", "span"]):
                s = t.get_text(" ", strip=True)
                s = self._clean_ws(s)
                if s:
                    texts.append(s)

            if not texts:
                # 인접 형제에서 일정 개수 추출
                sibs = []
                for sib in box.find_all_next(["li", "p", "dd", "td", "div", "span"], limit=60):
                    txt = self._clean_ws(sib.get_text(" ", strip=True))
                    if txt:
                        sibs.append(txt)
                texts = sibs

            # 노이즈 필터
            texts = [ln for ln in texts if not self._is_noise_line(ln)]
            # 너무 긴 줄은 문장 단위로 자르기
            refined = []
            for ln in texts:
                if len(ln) > 300:
                    parts = re.split(r"[•·\-\u2022\|\n]+", ln)
                    for p in parts:
                        p = self._clean_ws(p)
                        if p and not self._is_noise_line(p):
                            refined.append(p)
                else:
                    refined.append(ln)

            refined = self._dedup_lines_ordered(refined)
            if refined:
                candidates.append("\n".join(refined))

        if candidates:
            raw = max(candidates, key=len)  # 가장 긴 것을 섹션으로 간주
            raw = self._clean_ws(raw[:8000])  # 안전 길이 제한
            # 최종적으로 메뉴성/광고성 라인 추가 필터링
            final_lines = [ln for ln in raw.split("\n") if not self._is_noise_line(ln)]
            final = "\n".join(self._dedup_lines_ordered(final_lines))
            return final if final.strip() else None

        return None

    def _summarize_bullets(self, text: str, max_lines: int = 5) -> str:
        """
        간단 규칙 기반 요약:
        - 불릿/숫자/키워드 포함 라인 위주 선별
        - 전문/광고성 문구 제거
        """
        if not text:
            return ""

        lines = [self._clean_ws(x) for x in text.split("\n")]
        lines = [x for x in lines if x and not self._is_noise_line(x)]

        # 키워드 점수 기반 선별
        keywords = [
            "필수", "우대", "경력", "신입", "개발", "운영", "Python", "Java", "C++", "서버", "DB",
            "AWS", "OCI", "Linux", "네트워크", "보안", "자격", "학력", "전공", "우대사항", "담당업무",
            "근무", "근무지", "연봉", "협의", "정규직", "복리후생", "4대보험", "식대", "연차", "퇴직금",
            "포트폴리오", "자격증", "정보처리", "전문연", "병역", "군필"
        ]
        kw_re = re.compile("|".join([re.escape(k) for k in keywords]), re.IGNORECASE)

        scored = []
        for ln in lines:
            score = 0
            if re.search(r"^[\-\•\u2022\*\d]+\s", ln):  # 불릿/숫자 시작
                score += 2
            if len(ln) <= 140:
                score += 1
            if kw_re.search(ln):
                score += 3
            scored.append((score, ln))

        scored.sort(key=lambda x: (-x[0], len(x[1])))  # 점수↓, 길이↑
        picked = []
        used = set()
        for _, ln in scored:
            if ln in used:
                continue
            used.add(ln)
            picked.append(ln)
            if len(picked) >= max_lines:
                break

        if not picked:
            # fallback: 앞부분 상위 3~5줄
            picked = lines[:max_lines]

        # 불릿 포맷으로 정리
        return "• " + "\n• ".join([self._clean_ws(x) for x in picked])

    def _fetch_and_parse_detail(self, session: requests.Session, url: str) -> Tuple[str, Dict[str, str]]:
        """
        상세페이지 1건 요청+파싱. (세션/타임아웃/리트라이 내장)
        반환: (url, {employment_type, salary, requirements_raw, benefits_raw, requirements_summary, benefits_summary})
        """
        result = {
            "employment_type": "", "salary": "",
            "requirements_raw": "", "benefits_raw": "",
            "requirements_summary": "", "benefits_summary": ""
        }
        if not url:
            return url, result

        for _ in range(3):
            try:
                resp = session.get(url, timeout=20, headers=self.headers)
                if resp.status_code != 200:
                    time.sleep(0.4)
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")

                content = self._find_content_container(soup)
                self._strip_noise_nodes(content)

                # 라벨 기반 (고용형태/급여)
                emp = self._extract_label_value(content, ["고용형태", "근무형태"])
                sal = self._extract_label_value(content, ["급여", "연봉", "보수", "급여조건"])

                # 섹션 기반 (자격요건/복리후생)
                req = self._extract_section_text(
                    content,
                    ["자격요건", "지원자격", "필수요건", "우대사항", "우대조건", "모집요강", "담당업무"]
                )
                ben = self._extract_section_text(
                    content,
                    ["복리후생", "혜택", "지원제도", "회사복지"]
                )

                # 요약 생성
                req_sum = self._summarize_bullets(req or "", max_lines=5) if req else ""
                ben_sum = self._summarize_bullets(ben or "", max_lines=4) if ben else ""

                result.update({
                    "employment_type": emp or "",
                    "salary": sal or "",
                    "requirements_raw": req or "",
                    "benefits_raw": ben or "",
                    "requirements_summary": req_sum,
                    "benefits_summary": ben_sum
                })
                return url, result
            except Exception:
                time.sleep(0.6)
                continue

        return url, result  # 실패 시 빈 값

    def enrich_with_details(self, df: pd.DataFrame, max_workers: int = 8) -> pd.DataFrame:
        """
        멀티스레드로 상세페이지를 병렬 파싱하여 컬럼 추가 (원문 + 요약)
        """
        if df.empty:
            return df

        urls = df["link"].fillna("").tolist()
        results_map: Dict[str, Dict[str, str]] = {}

        with requests.Session() as session:
            session.headers.update(self.headers)
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {ex.submit(self._fetch_and_parse_detail, session, url): url for url in urls}
                for fut in as_completed(futures):
                    url, parsed = fut.result()
                    results_map[url] = parsed

        for col in [
            "employment_type", "salary",
            "requirements_raw", "benefits_raw",
            "requirements_summary", "benefits_summary"
        ]:
            df[col] = df["link"].map(lambda u: results_map.get(u, {}).get(col, ""))

        return df

    # ---------- HTML/이메일 ----------
    def build_html_page(self, df: pd.DataFrame, out_html_path: str, page_title: str = "채용공고 결과(정제+요약)"):
        out_path = Path(out_html_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        cols = [
            'title','company','location','career','education','deadline',
            'employment_type','salary',
            'requirements_summary','benefits_summary',
            'requirements_raw','benefits_raw',
            'link','crawled_at'
        ]
        exist_cols = [c for c in cols if c in df.columns]
        styled = df[exist_cols].rename(columns={
            'title':'제목','company':'회사','location':'위치','career':'경력',
            'education':'학력','deadline':'마감일','employment_type':'고용형태',
            'salary':'급여','requirements_summary':'자격요건(요약)','benefits_summary':'복리후생(요약)',
            'requirements_raw':'자격요건(원문)','benefits_raw':'복리후생(원문)',
            'link':'링크','crawled_at':'수집시각'
        }).copy()

        # 링크 컬럼 HTML로 변환
        if '링크' in styled.columns:
            styled['링크'] = styled['링크'].apply(lambda x: f'<a href="{x}" target="_blank">바로가기</a>' if x else '')

        # 원문은 줄바꿈 보존
        for c in ['자격요건(원문)', '복리후생(원문)', '자격요건(요약)', '복리후생(요약)']:
            if c in styled.columns:
                styled[c] = styled[c].astype(str).str.replace("\n", "<br>")

        table_html = styled.to_html(index=False, escape=False, justify="center", border=0)
        html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{page_title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }}
.container {{ max-width:1200px; margin:0 auto; }}
h1 {{ font-size:24px; margin-bottom:8px; }}
.desc {{ color:#666; margin-bottom:20px; }}
table {{ width:100%; border-collapse:collapse; table-layout:fixed; word-break:break-word; }}
th, td {{ border-bottom:1px solid #eee; padding:12px 8px; text-align:left; vertical-align:top; }}
th {{ background:#fafafa; font-weight:700; }}
tr:hover {{ background:#f4f9ff; }}
a {{ text-decoration:none; color:#3498db; }}
.meta {{ color:#888; font-size:13px; margin-top:20px; }}
.small {{ color:#888; font-size:12px; }}
</style>
</head>
<body>
<div class="container">
  <h1>{page_title}</h1>
  <div class="desc">생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
  <p class="small">* 메일에는 요약 위주로 표시되며, 본 페이지에서는 원문과 요약이 함께 제공됩니다.</p>
  {table_html}
  <div class="meta">Powered by Python · 자동 크롤링 · Updated everyday</div>
</div>
</body>
</html>"""
        out_path.write_text(html, encoding="utf-8")
        print(f"🌐 HTML 생성: {out_path}")
        return str(out_path)

    def generate_html_table_for_email(self, df: pd.DataFrame, max_rows=10):
        subset = df.head(max_rows).fillna("")
        cols = [
            "title","company","location","employment_type","salary",
            "requirements_summary","benefits_summary"
        ]
        exist = [c for c in cols if c in subset.columns]
        th_map = {
            "title":"제목","company":"회사","location":"위치",
            "employment_type":"고용형태","salary":"급여",
            "requirements_summary":"자격요건(요약)","benefits_summary":"복리후생(요약)"
        }
        thead = "".join([f"<th>{th_map.get(c,c)}</th>" for c in exist])
        html = "<table style='width:100%; border-collapse:collapse; margin-top:20px;'>"
        html += f"<tr style='background:#667eea; color:white;'>{thead}</tr>"
        for _, row in subset.iterrows():
            html += "<tr>"
            for c in exist:
                val = str(row.get(c, "")).replace("\n", "<br>")
                html += f"<td style='padding:8px 6px; border-bottom:1px solid #eee;'>{val}</td>"
            html += "</tr>"
        html += "</table>"
        return html

    def send_email(self, df: pd.DataFrame, sender_email, app_password, receiver_email, pages_url: str):
        """
        CSV 첨부 없이 HTML 본문 + 전체 페이지 링크만 전송
        """
        if df.empty:
            print("⚠ 전송할 공고가 없습니다.")
            return

        subject = f"🎯 채용공고 자동 수집 결과(정제+요약) - {datetime.now().strftime('%Y-%m-%d')}"
        html_table = self.generate_html_table_for_email(df, max_rows=10)

        html_body = f"""
        <html><head><meta charset="UTF-8"></head>
        <body style="font-family:'Apple SD Gothic Neo',Arial,sans-serif;">
        <h1>🎯 채용공고 자동 수집 결과 (정제+요약)</h1>
        <p>{datetime.now().strftime('%Y년 %m월 %d일')} 수집 완료</p>
        <div>
          <h2>📊 수집 현황</h2>
          <p>• <strong>총 {len(df)}개</strong> 공고 발견</p>
        </div>
        <div>
          <h2>🔥 주요 공고 미리보기 (최대 10개, 요약)</h2>
          {html_table}
        </div>
        <div style="text-align:center; margin:30px 0;">
          <a href="{pages_url}" style="display:inline-block; padding:12px 20px; background:#3498db; color:#fff; text-decoration:none; border-radius:6px;">🌐 전체 공고 보기 (원문+요약)</a>
        </div>
        <p style="font-size:12px; color:#888;">🤖 CSV 파일은 포함되지 않았습니다. 전체 데이터는 상단 링크에서 확인 가능합니다.</p>
        </body></html>
        """

        msg = MIMEMultipart('alternative')
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = subject
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        try:
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(sender_email, app_password)
            server.send_message(msg)
            server.quit()
            print("📧 이메일 전송 완료! (첨부 없음)")
        except Exception as e:
            print(f"❌ 이메일 전송 실패: {e}")


# -------------------------------
# 실행부
# -------------------------------
if __name__ == "__main__":
    crawler = SaraminCrawler()

    # 1) 검색 → 기본정보 수집
    df = crawler.crawl_all(sleep_sec=0.6, page_limit=None)
    if df.empty:
        print("종료: 수집 데이터 없음")
        raise SystemExit(0)

    # 2) 상세페이지 멀티스레드 파싱 (원문 정제 + 요약)
    print("🧩 상세페이지 파싱(멀티스레드) 시작...")
    df = crawler.enrich_with_details(df, max_workers=8)
    print("🧩 상세페이지 파싱 완료.")

    # 3) CSV 저장 (백업/검증용: 메일에는 첨부하지 않음)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_csv = f"saramin_results_raw_{ts}.csv"
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"✅ CSV 저장 완료: {len(df)} rows → {out_csv}")

    # 4) HTML 저장 (GitHub Pages용: 원문+요약 모두 포함)
    docs_dir = Path("docs")
    html_path = docs_dir / "saramin_results_latest.html"
    pages_url = "https://pkpjs.github.io/test/saramin_results_latest.html"  # 필요시 수정
    crawler.build_html_page(df, str(html_path))

    # 5) 이메일 발송 (환경변수 사용; 미설정 시 기본 수신자는 example@gmail.com)
    EMAIL_SENDER   = os.environ.get("EMAIL_SENDER")
    EMAIL_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")
    EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER", "example@gmail.com")

    if all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
        crawler.send_email(
            df=df,
            sender_email=EMAIL_SENDER,
            app_password=EMAIL_PASSWORD,
            receiver_email=EMAIL_RECEIVER,
            pages_url=pages_url
        )
    else:
        print("ℹ️ 이메일 발송 생략: EMAIL_SENDER / EMAIL_APP_PASSWORD (그리고 선택적으로 EMAIL_RECEIVER)가 필요합니다.")

    print(f"🔗 전체 공고 페이지 주소: {pages_url}")
