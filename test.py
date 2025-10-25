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
from email.mime.application import MIMEApplication
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Tuple, Optional, List, Set

# -------------------------------
# Saramin Crawler (검색 + 상세 파싱 + 요약)
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
        # 🔎 검색 조건(요청하신 조건)
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

        # -------- 요약용 사전(키워드) --------
        # 기술/스택
        self.skill_keywords = [
            # 언어/프레임워크
            "python","java","javascript","typescript","go","golang","c","c\\+\\+","c#","scala","ruby","kotlin","swift","php",
            "node","node\\.js","react","react\\.js","vue","vue\\.js","angular","spring","springboot","django","flask",
            ".net","asp\\.net","jpa","hibernate","mybatis",
            # 데이터/플랫폼
            "sql","mysql","postgresql","postgres","mariadb","mssql","oracle","redis","mongodb","elasticsearch","kafka",
            "hadoop","spark","hive","presto","airflow","storm","kinesis","flink","redshift","bigquery","snowflake",
            # 인프라/클라우드/도구
            "aws","gcp","azure","linux","unix","docker","kubernetes","terraform","ansible","nginx","apache","jenkins",
            "git","gitlab","github","ci/cd","cicd","prometheus","grafana","vault",
            # ML/AI/보안
            "pytorch","tensorflow","sklearn","scikit-learn","opencv","nlp","머신러닝","딥러닝","ai","ml","llm","rag",
            "보안","침투테스트","모의해킹","waf","siem","ids","ips","sso","oauth","saml","kms","kms","kms",
            # 기타
            "etl","bi","spark streaming","airflow","metabase","tableau","looker","power bi","powerbi","superset"
        ]
        # 학력/자격증/연차/형태
        self.req_patterns = {
            "degree": [r"고졸", r"초대졸", r"전문학사", r"학사", r"대졸", r"석사", r"박사"],
            "years": [r"(\d+)\s*년(?:\s*이상|\s*이하|\s*경력)?", r"경력\s*(\d+)\s*년"],
            "newbie": [r"신입", r"경력\s*무관", r"무관"],
            "certs": [
                r"정보처리기사", r"정보보안기사", r"SQLD", r"ADsP", r"ADP", r"OCP", r"CCNA", r"TOEIC",
                r"AWS[-\s]?SAA", r"AWS[-\s]?SAP", r"AWS[-\s]?DVA", r"GCP[-\s]?PCA", r"CKA", r"CKAD"
            ],
            "must_prefer": [r"필수", r"우대", r"우대사항", r"우대조건", r"가산점", r"가점"]
        }
        # 복리후생 키워드
        self.benefit_keywords = [
            "재택", "원격", "하이브리드", "유연근무", "탄력근무", "시차출근", "주4일", "주4.5일",
            "연봉인상", "성과급", "인센티브", "스톡옵션", "복지포인트", "명절선물",
            "중식", "석식", "야근식대", "식대", "간식", "사내카페",
            "교통비", "통신비", "장비지원", "최신장비",
            "4대보험", "퇴직금", "퇴직연금", "단체보험", "상해보험",
            "연차", "반차", "리프레시", "장기근속", "휴가비",
            "건강검진", "헬스", "사우나", "의료비",
            "자기계발비", "교육비", "세미나", "스터디", "도서구입",
            "기숙사", "기숙사제공", "사택", "주거지원",
            "경조사", "경조금", "경조휴가", "출산", "육아휴직", "돌봄",
            "사내동아리", "워라밸"
        ]

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

    # ---------- 상세페이지 파싱 ----------
    def _extract_label_block(self, soup: BeautifulSoup, label_keywords) -> Optional[str]:
        """
        상세페이지의 다양한 마크업에서 '고용형태/급여' 같은 라벨-값 구조 추출.
        """
        text_nodes = soup.find_all(string=re.compile("|".join(label_keywords)))
        for node in text_nodes:
            text = node.strip()
            parent = node.parent
            # 1) dt -> dd
            if parent.name == "dt":
                dd = parent.find_next_sibling("dd")
                if dd:
                    return dd.get_text(" ", strip=True)
            # 2) th -> td
            if parent.name == "th":
                td = parent.find_next_sibling("td")
                if td:
                    return td.get_text(" ", strip=True)
            # 3) strong/label 등 -> 다음 형제
            sib = parent.find_next_sibling()
            if sib and sib.name in ["dd", "td", "p", "div", "span"]:
                val = sib.get_text(" ", strip=True)
                if val:
                    return val
            # 4) 같은 줄 콜론/스페이스 분리
            line = parent.get_text(" ", strip=True)
            for kw in label_keywords:
                if kw in line:
                    after = line.split(kw, 1)[1].strip(" :-—\t")
                    if after:
                        return after
        return None

    def _extract_long_section(self, soup: BeautifulSoup, keywords_regex: str) -> Optional[str]:
        """
        자격요건 / 복리후생 등 긴 텍스트 섹션을 최대한 넓게 긁어와 원문 반환.
        """
        candidates = []
        section_nodes = soup.find_all(string=re.compile(keywords_regex))
        for node in section_nodes:
            box = node
            # 상위로 올려 섹션 컨테이너 추정
            for _ in range(2):
                if box and box.parent:
                    box = box.parent
            if not box:
                continue
            texts = [t.get_text(" ", strip=True)
                     for t in box.find_all(["li", "p", "dd", "td", "div", "span"])
                     if t.get_text(strip=True)]
            if not texts:
                sibs = []
                for sib in box.find_all_next(["li", "p", "dd", "td", "div", "span"], limit=30):
                    txt = sib.get_text(" ", strip=True)
                    if txt:
                        sibs.append(txt)
                texts = sibs
            chunk = "\n".join(texts)
            if chunk:
                candidates.append(chunk)
        if candidates:
            return max(candidates, key=len)[:6000]
        return None

    def _fetch_and_parse_detail(self, session: requests.Session, url: str) -> Tuple[str, Dict[str, str]]:
        """
        상세페이지 1건 요청+파싱. (세션/타임아웃/리트라이 내장)
        반환: (url, {employment_type, salary, requirements_raw, benefits_raw})
        """
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

                emp = self._extract_label_block(soup, ["고용형태"])
                sal = self._extract_label_block(soup, ["급여", "연봉", "보수"])

                req = self._extract_long_section(soup, r"(자격요건|필수요건|우대사항|우대조건)")
                ben = self._extract_long_section(soup, r"(복리후생|혜택|지원제도)")

                result["employment_type"] = emp or ""
                result["salary"]          = sal or ""
                result["requirements_raw"] = req or ""
                result["benefits_raw"]     = ben or ""
                return url, result
            except Exception:
                time.sleep(0.6)
                continue

        return url, result  # 실패 시 빈 값

    def enrich_with_details(self, df: pd.DataFrame, max_workers: int = 8) -> pd.DataFrame:
        """
        멀티스레드로 상세페이지를 병렬 파싱하여 컬럼 추가
        """
        if df.empty:
            return df

        urls = df["link"].fillna("").tolist()
        results_map = {}

        with requests.Session() as session:
            session.headers.update(self.headers)
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {ex.submit(self._fetch_and_parse_detail, session, url): url for url in urls}
                for fut in as_completed(futures):
                    url, parsed = fut.result()
                    results_map[url] = parsed

        for col in ["employment_type", "salary", "requirements_raw", "benefits_raw"]:
            df[col] = df["link"].map(lambda u: results_map.get(u, {}).get(col, ""))

        return df

    # ---------- 요약(핵심 키워드 전부 추출) ----------
    @staticmethod
    def _normalize_text(t: str) -> str:
        t = re.sub(r"[ \t]+", " ", t)
        t = t.replace("\r", "\n")
        t = re.sub(r"\n{2,}", "\n", t)
        return t.strip()

    def _extract_keywords(self, text: str, patterns: Dict[str, List[str]]) -> Set[str]:
        """패턴 dict로 일치하는 항목 전부 추출"""
        hits: Set[str] = set()
        for _, regs in patterns.items():
            for rgx in regs:
                for m in re.findall(rgx, text, flags=re.IGNORECASE):
                    if isinstance(m, tuple):
                        m = " ".join([x for x in m if x])
                    hits.add(str(m).strip())
        return hits

    def _extract_skills(self, text: str) -> Set[str]:
        """스킬 키워드 전부 추출 (대소문자/구두점 무시)"""
        t = text.lower()
        # 구분자 정리
        t = re.sub(r"[,/|·•・•\-\(\)\[\]{}]", " ", t)
        found: Set[str] = set()
        for kw in self.skill_keywords:
            if re.search(rf"(?<![a-z0-9+]){kw}(?![a-z0-9+])", t, flags=re.IGNORECASE):
                # 표기 통일
                norm = kw.replace("\\+\\+", "C++").replace("\\.", ".")
                norm = norm.upper() if norm in ["aws","gcp","sql","ci/cd","cicd","nlp","ai","ml"] else norm
                # 보기 좋게 대문자화/타이틀화
                if norm.lower() in ["python","java","javascript","typescript","golang","node","react","vue","angular","django","flask","spring","springboot"]:
                    norm = norm.title().replace("Javascript","JavaScript").replace("Typescript","TypeScript")
                found.add(norm)
        return found

    def summarize_requirements(self, raw: str) -> str:
        if not raw:
            return ""
        text = self._normalize_text(raw)
        skills  = self._extract_skills(text)
        extras  = self._extract_keywords(text, self.req_patterns)

        # '필수/우대' 문구는 그대로 추가
        # 연차/학력/자격증/신입여부 등은 extras에 들어있음
        # 보기좋게 정렬
        out = sorted(skills | extras, key=lambda s: s.lower())
        return " / ".join(out)

    def summarize_benefits(self, raw: str) -> str:
        if not raw:
            return ""
        text = self._normalize_text(raw)
        # 키워드 전부 포함
        hits: Set[str] = set()
        for kw in self.benefit_keywords:
            if re.search(kw, text, flags=re.IGNORECASE):
                hits.add(kw)
        out = sorted(hits, key=lambda s: s.lower())
        return " / ".join(out)

    def add_summaries(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df["requirements_summary"] = df["requirements_raw"].apply(self.summarize_requirements)
        df["benefits_summary"]     = df["benefits_raw"].apply(self.summarize_benefits)
        return df

    # ---------- HTML/이메일 ----------
    def build_html_page(self, df: pd.DataFrame, out_html_path: str, page_title: str = "채용공고 결과(요약형)"):
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

        # 링크 컬럼 HTML로 변환
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
table {{ width:100%; border-collapse:collapse; }}
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
        print(f"🌐 HTML 생성: {out_path}")
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
                val = str(row.get(c, ""))
                html += f"<td style='padding:8px 6px; border-bottom:1px solid #eee;'>{val}</td>"
            html += "</tr>"
        html += "</table>"
        return html

    def send_email(self, df: pd.DataFrame, csv_path, sender_email, app_password, receiver_email, pages_url: str):
        if df.empty:
            print("⚠ 전송할 공고가 없습니다.")
            return

        subject = f"🎯 채용공고 자동 수집 결과(요약형) - {datetime.now().strftime('%Y-%m-%d')}"
        html_table = self.generate_html_table_for_email(df, max_rows=10)

        html_body = f"""
        <html><head><meta charset="UTF-8"></head>
        <body style="font-family:'Apple SD Gothic Neo',Arial,sans-serif;">
        <h1>🎯 채용공고 자동 수집 결과 (요약형)</h1>
        <p>{datetime.now().strftime('%Y년 %m월 %d일')} 수집 완료</p>
        <div>
          <h2>📊 수집 현황</h2>
          <p>• <strong>총 {len(df)}개</strong> 공고 발견</p>
        </div>
        <div>
          <h2>🔥 주요 공고 미리보기 (최대 10개)</h2>
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

        # CSV 첨부
        if csv_path and os.path.exists(csv_path):
            with open(csv_path, 'rb') as f:
                part = MIMEApplication(f.read(), _subtype='csv')
                part.add_header('Content-Disposition', 'attachment', filename=os.path.basename(csv_path))
                msg.attach(part)

        try:
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(sender_email, app_password)
            server.send_message(msg)
            server.quit()
            print("📧 이메일 전송 완료!")
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

    # 2) 상세페이지 멀티스레드 파싱 (원문 수집)
    print("🧩 상세페이지 파싱(멀티스레드) 시작...")
    df = crawler.enrich_with_details(df, max_workers=8)
    print("🧩 상세페이지 파싱 완료.")

    # 3) 요약 컬럼 생성 (핵심 키워드 전부)
    df = crawler.add_summaries(df)

    # 4) CSV 저장
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_csv = f"saramin_results_summary_{ts}.csv"
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"✅ CSV 저장 완료: {len(df)} rows → {out_csv}")

    # 5) HTML 저장 (GitHub Pages용)
    docs_dir = Path("docs")
    html_path = docs_dir / "saramin_results_latest.html"
    pages_url = "https://pkpjs.github.io/test/saramin_results_latest.html"  # 필요시 수정
    crawler.build_html_page(df, str(html_path))

    # 6) 이메일 발송 (환경변수 사용; 미설정 시 기본 수신자는 example@gmail.com)
    EMAIL_SENDER   = os.environ.get("EMAIL_SENDER")
    EMAIL_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")
    EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER", "example@gmail.com")

    if all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
        crawler.send_email(
            df=df,
            csv_path=out_csv,
            sender_email=EMAIL_SENDER,
            app_password=EMAIL_PASSWORD,
            receiver_email=EMAIL_RECEIVER,
            pages_url=pages_url
        )
    else:
        print("ℹ️ 이메일 발송 생략: EMAIL_SENDER / EMAIL_APP_PASSWORD (그리고 선택적으로 EMAIL_RECEIVER)가 필요합니다.")

    print(f"🔗 전체 공고 페이지 주소: {pages_url}")
