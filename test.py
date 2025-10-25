import math
import time
import os
import re
import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Tuple, Optional, List


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

    @staticmethod
    def _clean_text(text: str) -> str:
        if not text:
            return ""
        t = text.replace("\r", "\n")
        t = re.sub(r"\u00A0", " ", t)
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\n{2,}", "\n", t)
        return t.strip()

    @staticmethod
    def _clean_ws(text: str) -> str:
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _dedup_lines_ordered(lines: List[str]) -> List[str]:
        seen = set()
        out = []
        for ln in lines:
            k = ln.strip()
            if k and k not in seen:
                out.append(k)
                seen.add(k)
        return out

    @staticmethod
    def _is_noise_line(t: str) -> bool:
        if not t:
            return True
        bad = [
            "로그인", "회원가입", "기업서비스", "TOP", "고객센터", "이벤트", "도움말",
            "사람인", "커리어피드", "인적성", "스토어", "면접 코칭", "자소서", "클래스",
            "공지사항", "검색", "홈", "채용정보", "기업·연봉", "커뮤니티", "SNS", "공유",
            "Copyright", "사업자", "FAX", "help@saramin.co.kr"
        ]
        if len(t) < 3:
            return True
        if any(b in t for b in bad):
            return True
        if re.fullmatch(r"[\-\–\—\|•·]+", t):
            return True
        return False

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
        all_jobs = []
        first_page_jobs, total_count = self._fetch_page(1)
        if not first_page_jobs:
            return pd.DataFrame()
        all_jobs.extend(first_page_jobs)
        page_count = math.ceil(total_count / int(self.params["recruitPageCount"])) if total_count else 1
        if page_limit is not None:
            page_count = min(page_count, page_limit)
        for p in range(2, page_count + 1):
            jobs, _ = self._fetch_page(p)
            if not jobs:
                break
            all_jobs.extend(jobs)
            time.sleep(sleep_sec)
        df = pd.DataFrame(all_jobs)
        if df.empty:
            return df
        if "rec_idx" in df.columns:
            df["__dedup_key"] = df["rec_idx"].where(df["rec_idx"].astype(bool), other=df["link"])
            df.drop_duplicates(subset=["__dedup_key"], inplace=True)
            df.drop(columns=["__dedup_key"], inplace=True)
        else:
            df.drop_duplicates(subset=["link"], inplace=True)
        return df

    def _extract_label_value(self, soup: BeautifulSoup, labels: List[str]) -> Optional[str]:
        nodes = soup.find_all(string=re.compile("|".join([re.escape(x) for x in labels])))
        for node in nodes:
            parent = node.parent
            if not parent:
                continue
            if parent.name == "dt":
                dd = parent.find_next_sibling("dd")
                if dd:
                    return self._clean_text(dd.get_text(" ", strip=True))
            if parent.name == "th":
                td = parent.find_next_sibling("td")
                if td:
                    return self._clean_text(td.get_text(" ", strip=True))
            sib = parent.find_next_sibling()
            if sib and sib.name in ["dd", "td", "p", "div", "span"]:
                val = self._clean_text(sib.get_text(" ", strip=True))
                if val:
                    return val
            line = self._clean_text(parent.get_text(" ", strip=True))
            for kw in labels:
                if kw in line:
                    after = line.split(kw, 1)[1].lstrip(": -—\t")
                    if after:
                        return self._clean_text(after)
        return None

    def _find_content_container(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        candidates = [
            "#content .wrap_jv_cont",
            "#content .jv_cont",
            ".wrap_jv_cont",
            ".jv_cont",
            "#recruit_info",
            ".content",
            "section[class*=jv]",
            "div[class*=jv_]",
        ]
        best_node = None
        best_length = 0
        for selector in candidates:
            nodes = soup.select(selector)
            for node in nodes:
                text = node.get_text(" ", strip=True)
                if text and len(text) > best_length:
                    best_length = len(text)
                    best_node = node
        if not best_node:
            best_node = soup.select_one("#content") or soup.body or soup
        self._strip_noise_nodes(best_node)
        return best_node

    def _strip_noise_nodes(self, node: BeautifulSoup):
        for tag in node.find_all(["script", "style", "noscript", "header", "footer", "nav", "aside", "iframe"]):
            tag.decompose()
        junk_keywords = [
            "sns", "share", "banner", "ad", "advert", "login", "회원",
            "기업서비스", "검색", "TOP", "footer", "고객센터", "이벤트",
            "사람인스토어", "취업TOOL", "헤드헌팅", "인적성검사", "커뮤니티",
            "회사소개", "인재채용", "회원약관", "개인정보처리방침", "위치기반",
            "제휴문의", "도움말", "FAQ", "MY", "스크랩", "지원현황", "최근본"
        ]
        for div in node.find_all("div"):
            text = div.get_text(" ", strip=True)
            if any(jk in text for jk in junk_keywords) and len(text) < 300:
                div.decompose()

    def _extract_section(self, content_node: BeautifulSoup, title_keywords: List[str]) -> Optional[str]:
        if not content_node:
            return None
        regex = re.compile("|".join([re.escape(x) for x in title_keywords]), re.IGNORECASE)
        hits = content_node.find_all(string=regex)
        results = []
        for hit in hits:
            section = hit.find_parent()
            for _ in range(3):
                if section and section.parent:
                    section = section.parent
            if not section:
                continue
            texts = []
            for tag in section.find_all(["li", "p", "div", "td", "span"]):
                t = tag.get_text(" ", strip=True)
                if t and not self._is_noise_line(t):
                    texts.append(self._clean_ws(t))
            lines = []
            for text in texts:
                if len(text) > 200:
                    parts = re.split(r"[•·\u2022\-\|\n]+", text)
                    for p in parts:
                        p = p.strip()
                        if p and not self._is_noise_line(p):
                            lines.append(p)
                else:
                    lines.append(text)
            lines = self._dedup_lines_ordered(lines)
            if lines and len("\n".join(lines)) > 50:
                results.append("\n".join(lines))
        if results:
            return max(results, key=len)
        return None

    def _summarize(self, text: str, max_lines=5) -> str:
        if not text:
            return ""
        lines = [self._clean_ws(x) for x in text.split("\n") if x.strip()]
        lines = [x for x in lines if not self._is_noise_line(x)]
        keywords = ["경력", "신입", "우대", "필수", "개발", "운영", "학력", "전공", "자격증", "포트폴리오", "4대보험", "연차", "복지", "급여", "지원", "근무", "기술", "언어", "스택"]
        scored = []
        for ln in lines:
            score = sum(1 for kw in keywords if kw in ln)
            if re.match(r"^[•\-*\d]+\s*", ln):
                score += 2
            if len(ln) < 150:
                score += 1
            scored.append((score, ln))
        scored.sort(key=lambda x: (-x[0], len(x[1])))
        picked = [ln for _, ln in scored[:max_lines]]
        return "• " + "\n• ".join(picked) if picked else ""

    def _fetch_and_parse_detail(self, session: requests.Session, url: str) -> Tuple[str, Dict[str, str]]:
        result = {"employment_type": "", "salary": "", "requirements_raw": "", "benefits_raw": ""}
        if not url:
            return url, result
        for _ in range(3):
            try:
                resp = session.get(url, timeout=20, headers=self.headers)
                if resp.status_code != 200:
                    time.sleep(0.4)
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")
                content_root = self._find_content_container(soup)
                emp = self._extract_label_value(content_root, ["고용형태", "근무형태"]) or self._extract_label_value(soup, ["고용형태", "근무형태"])
                sal = self._extract_label_value(content_root, ["급여", "연봉", "보수", "급여조건"]) or self._extract_label_value(soup, ["급여", "연봉", "보수", "급여조건"])
                req = self._extract_section(content_root, ["자격요건", "지원자격", "필수요건", "우대사항", "우대조건", "모집요강", "담당업무", "직무내용"])
                ben = self._extract_section(content_root, ["복리후생", "혜택", "지원제도", "회사복지", "근무환경"])
                result["employment_type"] = emp or ""
                result["salary"] = sal or ""
                result["requirements_raw"] = req or ""
                result["benefits_raw"] = ben or ""
                return url, result
            except Exception:
                time.sleep(0.6)
                continue
        return url, result

    def enrich_with_details(self, df: pd.DataFrame, max_workers: int = 8) -> pd.DataFrame:
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
        for col in ["employment_type", "salary", "requirements_raw", "benefits_raw"]:
            df[col] = df["link"].map(lambda u: results_map.get(u, {}).get(col, ""))
        df["requirements_summary"] = df["requirements_raw"].apply(lambda x: self._summarize(x, max_lines=5))
        df["benefits_summary"] = df["benefits_raw"].apply(lambda x: self._summarize(x, max_lines=4))
        return df

    def build_html_page(self, df: pd.DataFrame, out_html_path: str, page_title: str = "채용공고 결과(요약)"):
        out_path = Path(out_html_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cols = [
            'title','company','location','career','education','deadline',
            'employment_type','salary','requirements_summary','benefits_summary',
            'link','crawled_at'
        ]
        exist_cols = [c for c in cols if c in df.columns]
        styled = df[exist_cols].rename(columns={
            'title':'제목','company':'회사','location':'위치','career':'경력',
            'education':'학력','deadline':'마감일','employment_type':'고용형태',
            'salary':'급여','requirements_summary':'자격요건(요약)','benefits_summary':'복리후생(요약)',
            'link':'링크','crawled_at':'수집시각'
        }).copy()
        if '링크' in styled.columns:
            styled['링크'] = styled['링크'].apply(lambda x: f'<a href="{x}" target="_blank">바로가기</a>' if x else '')
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
</style>
</head>
<body>
<div class="container">
  <h1>{page_title}</h1>
  <div class="desc">생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
  {table_html}
  <div class="meta">Powered by Python · 자동 크롤링 · Updated everyday</div>
</div>
</body>
</html>"""
        out_path.write_text(html, encoding="utf-8")
        return str(out_path)

    def generate_html_table_for_email(self, df: pd.DataFrame, max_rows=10):
        subset = df.head(max_rows).fillna("")
        cols = ["title","company","location","employment_type","salary","requirements_summary","benefits_summary"]
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
        if df.empty:
            print("⚠ 전송할 공고가 없습니다.")
            return
        subject = f"🎯 채용공고 자동 수집 결과(요약) - {datetime.now().strftime('%Y-%m-%d')}"
        html_table = self.generate_html_table_for_email(df, max_rows=10)
        html_body = f"""
        <html><head><meta charset="UTF-8"></head>
        <body style="font-family:'Apple SD Gothic Neo',Arial,sans-serif;">
        <h1>🎯 채용공고 자동 수집 결과 (요약)</h1>
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
          <a href="{pages_url}" style="display:inline-block; padding:12px 20px; background:#3498db; color:#fff; text-decoration:none; border-radius:6px;">🌐 전체 공고 보기</a>
        </div>
        <p style="font-size:12px; color:#888;">🤖 Python 자동화 시스템이 수집했습니다</p>
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


if __name__ == "__main__":
    crawler = SaraminCrawler()
    df = crawler.crawl_all(sleep_sec=0.6, page_limit=None)
    if df.empty:
        print("종료: 수집 데이터 없음")
        raise SystemExit(0)
    print("🧩 상세페이지 파싱(멀티스레드) 시작...")
    df = crawler.enrich_with_details(df, max_workers=8)
    print("🧩 상세페이지 파싱 완료.")

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_csv = f"saramin_results_{ts}.csv"
    try:
        df.to_csv(out_csv, index=False, encoding="utf-8-sig")
        print(f"✅ CSV 저장 완료: {len(df)} rows → {out_csv}")
    except Exception as e:
        print(f"CSV 저장 실패: {e}")

    docs_dir = Path("docs")
    html_path = docs_dir / "saramin_results_latest.html"
    pages_url = "https://pkpjs.github.io/test/saramin_results_latest.html"
    crawler.build_html_page(df, str(html_path))

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
