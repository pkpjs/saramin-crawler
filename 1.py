# -*- 코딩: utf-8 -*-
"""Update job application status based on Gmail "지원 완료" emails.

이 스크립트는 애플리케이션이 실행되었음을 나타내는 Gmail 메시지를 조회합니다.
결과를 제출하고 ``docs/application_status.json``에 유지하므로
파이프라인의 다른 부분(크롤러/페이지)은 최신 상태를 참조할 수 있습니다.
정보.

이 대본은 의도적으로 방어적입니다.
- 자격 증명은 ``GMAIL_TOKEN_JSON`` 환경 변수에서 읽힙니다.
  (GitHub 작업에 유용함) 또는 수동을 위한 로컬 ``token.json`` 파일에서
  달린다.
- 자격 증명이 누락된 경우 스크립트가 정상적으로 종료되므로 CI 작업이 진행됩니다.
  실패하지 않습니다.
- 메시지 페이로드 파싱은 다중 파트 및 일반 텍스트 페이로드와 함께 작동합니다.
  이메일 본문에 구조화된 필드가 포함되어 있지 않으면 제목으로 돌아갑니다.
"""

__future__ 에서 주석
 가져오기

base64
 가져오기
JSON
 가져오기
os
 가져오기
수입 재
datetime 에서 datetime, timezone
 가져오기
email.utils 에서 parsedate_to_datetime을
 가져옵니다 .
pathlib 에서 Path
 가져오기
import Dict , Iterable, List , Optional , Tuple을 입력 하여 

google.oauth2.credentials 에서 Credentials
 가져오기
googleapiclient.discovery 에서 빌드
 가져오기
googleapiclient.errors 에서 HttpError
 가져오기

범위 = [ "https://www.googleapis.com/auth/gmail.readonly" ]
STATUS_PATH = 경로( "docs/application_status.json" )
GMAIL_QUERY = "subject:(지원 완료 OR 입사지원 OR 접수완료 OR 지원이 완료되었습니다)"
최대 메시지 수 = 200


def normalize_text ( 값: 선택 사항 [ str ] ) -> str :
 
    """퍼지 매칭을 위해 텍스트를 정규화합니다.

    텍스트를 소문자로 변환하고 앞뒤 공백을 제거한 후 여러 텍스트를 축소합니다.
    회사/직책 비교가 더 관대해지도록 공백을 하나로 통합했습니다.
    """

    값이 아닌 경우 :
 
        반품 "" 
    축소됨 = re.sub( r"\s+" , " " , value.strip())
    collapsed.lower()를
 반환합니다 .


def load_credentials () -> 선택 사항 [자격 증명]:
 
    "env(권장) 또는 로컬 토큰 파일에서 Gmail 자격 증명을 로드합니다."

    token_env = os.environ.get( "GMAIL_TOKEN_JSON" )
    token_env
 의 경우 :
        노력하다 :
            정보 = json.loads(토큰_환경)
            Credentials.from_authorized_user_info(정보, 범위=SCOPES)를
 반환합니다 .
        json.JSONDecodeError를 예외 로 지정합니다 :   # pragma: no cover - defence
            raise RuntimeError("GMAIL_TOKEN_JSON 환경변수를 JSON으로 파싱할 수 없습니다.") from exc

    토큰 파일 = 경로( "토큰.json" )
    token_file.exists()
 가 있는 경우 :
        Credentials.from_authorized_user_file( str (토큰 파일), 범위=SCOPES)을
 반환합니다 .
    없음을 반환합니다 


def decode_message_data ( 데이터: 선택 사항 [ str ] ) -> str :
 
    """base64url로 인코딩된 Gmail 본문 조각을 디코딩합니다."""

    데이터가 아닌 경우 :
 
        반품 "" 
    패딩 = (- len (데이터)) % 4
    패딩 의 경우 :
        데이터 += "=" * 패딩
    노력하다 :
        base64.urlsafe_b64decode(data.encode( "utf-8" )).decode( "utf-8" , errors= "ignore" )를
 반환합니다 .
    (ValueError, UnicodeDecodeError   제외 ): # pragma: 커버 없음 - 방어적
        반품 "" 


def extract_body ( payload: Optional [ Dict ] ) -> str :
 
    """Gmail 메시지 페이로드에서 첫 번째 텍스트 본문을 추출합니다."""

    탑재물이 아닌 경우 :
 
        반품 "" 

    parts = payload.get( "parts" , []) 또는 []
    일부 의 일부 에 대해 :
        mime = part.get( "mimeType" , "" )
        mime.startswith( "text/plain" ) 인 경우 :
            body_data = part.get( "본문" , {}).get( "데이터" )
            텍스트 = 디코딩_메시지_데이터(본문_데이터)
            텍스트의 경우 :
                반환 텍스트

    일부 의 일부 에 대해 :
        중첩 = extract_body(부분)
        중첩된 경우 :
            중첩된 값
 을 반환합니다 .

    body_data = payload.get( "본문" , {}).get( "데이터" )
    decode_message_data(body_data)를
 반환합니다 .


def get_header ( 헤더: Iterable[ Dict [ str , str ]], name: str ) -> str :
 
    """대소문자를 구분하지 않고 헤더 값을 반환합니다."""

    헤더 의 헤더에 대해 :
        header.get( "name" , "" ).lower() == name.lower()
 인 경우 :
            header.get( "value" , ​​"" )
 을 반환합니다 .
    반품 "" 


def parse_company_title ( 제목: str , 본문: str ) -> 튜플 [ str , str ]:
 
    """이메일 본문/제목에서 회사/직함 값을 추출해 보세요."""

    회사 = ""
    제목 = ""

    ln.strip() 의 경우 body.splitlines () 에 대해 ln.strip()
 의 경우 :
        회사가 아닌 경우 :
 
            match = re.search(r"(?:회사|기업)명\s*[:：]\s*(.+)", line)
            일치하는 경우 :
 
                회사 = match .group( 1 ).strip()
                계속하다
        제목이 아닌 경우 :
 
            match = re.search(r"(?:공고|지원|채용)\s*명\s*[:：]\s*(.+)", line)
            일치하는 경우 :
 
                제목 = .group( 1 ).strip()
 과 일치
                계속하다
        제목이 아닌 경우 :
 
            match = re.search(r"(?:지원(?:분야|직무))\s*[:：]\s*(.+)", line)
            일치하는 경우 :
 
                제목 = .group( 1 ).strip()
 과 일치

    회사가 아니 거나 직함이 아닌 경우 :
  
        주제_패턴 = [
            re.compile(r"\[(?P<company>.+?)\]\s*(?P<title>.+?)\s*(?:지원\s*완료|입사지원)", re.IGNORECASE),
            re.compile(r"(?P<company>.+?)\s*-\s*(?P<title>.+?)\s*(?:지원\s*완료|입사지원)", re.IGNORECASE),
        ]
        subject_patterns 의 패턴 에 대해 :
            매치 = 패턴.검색(제목)
            일치하는 경우 :
 
                회사가 아닌 경우 :
 
                    회사 = match .group( "회사" ) .strip()
                제목이 아닌 경우 :
 
                    제목 = match .group( "title" ).strip()
                부서지다

    제목 과 주제가 아닌 경우 :
 
        제목 = 제목.strip()

    회사
 반환 , 직함


def parse_email_datetime ( date_value: str ) -> 선택 사항 [ str ]:
 
    """원시 Date 헤더를 ISO 8601 UTC 문자열로 변환합니다."""

    date_value가 아닌 경우 :
 
        없음을 반환합니다 
    노력하다 :
        dt = 날짜_시간_날짜_값(날짜_값)
    Except (TypeError, ValueError):   # pragma: no Cover - 방어적
        없음을 반환합니다 
    dt가 None 인 경우 :
 
        없음을 반환합니다 
    dt.tzinfo 가 None 인 경우 :
 
        dt = dt.replace(tzinfo=시간대.utc)
    dt.astimezone(timezone.utc).isoformat()을
 반환합니다 .


def load_status_data () -> Dict [ str , object ]:
 
    """지속된 상태 데이터 구조를 로드합니다."""

    STATUS_PATH.exists()
 가 있는 경우 :
        노력하다 :
            데이터 = json.loads(STATUS_PATH.read_text(인코딩= "utf-8" ))
        json.JSONDecodeError 를 제외하고 :   # pragma: 커버 없음 - 방어적
            데이터 = {}
        isinstance (data, dict )
 인 경우 : 
            data.setdefault( "메시지" , [])
            데이터
 반환
    { "메시지" : []}
 를 반환합니다 .


def save_status_data ( 데이터: Dict [ str , 객체 ] ) -> None :
 
    STATUS_PATH.parent.mkdir(부모= True , exist_ok= True )
    STATUS_PATH.write_text(json.dumps(데이터, ensure_ascii= False , 들여쓰기= 2 ), 인코딩= "utf-8" )


def fetch_new_messages ( 서비스, 알려진_ID: set [ str ] ) -> List [ Dict [ str , 객체 ]]:
 
    """``known_ids``에 아직 없는 새로운 Gmail 메시지를 가져옵니다."""

    가져온 ID: 목록 [ str ] = []
    next_token: 선택 사항 [ str ] = 없음

    참인 동안 :
 
        노력하다 :
            응답 = 서비스.사용자().메시지(). 목록 (
                사용자 ID = "나" ,
                q=GMAIL_QUERY,
                페이지 토큰=다음 토큰,
                최대 결과 = 100 ,
            ).실행하다()
        HttpError를 예외 로 제외 :   # pragma: no cover - 네트워크 안전
            print(f"❌ Gmail API 오류로 상태 업데이트를 중단합니다: {exc}")
            반품 []

        메시지 = response.get( "메시지" , [])
        메시지 의 메시지 :
            msg_id = 메시지.get( "id" )
            known_ids
 에 msg_id 또는 msg_id가 없는 경우 : 
                계속하다
            fetched_ids.append(msg_id)
            len (fetched_ids) >= MAX_MESSAGES
 인 경우 : 
                부서지다

        다음_토큰 = 응답.get( "다음페이지토큰" )
        next_token이 아니 거나 len (fetched_ids) >= MAX_MESSAGES
 인 경우 :  
            부서지다

    new_entries: 목록 [ 사전 [ 문자열 , 객체 ]] = []
    fetched_ids 의 msg_id 에 대해 :
        노력하다 :
            메시지 = 서비스.사용자().메시지().get(사용자ID= "나" , ID =msg_id, 형식 = "전체" ).실행()
        HttpError를 예외 로 제외 :   # pragma: no cover - 네트워크 안전
            print(f"⚠️ 메시지 {msg_id} 상세 조회에 실패했습니다: {exc}")
            계속하다

        페이로드 = 메시지.get( "페이로드" , {})
        헤더 = payload.get( "헤더" , [])
        제목 = get_header(헤더, "제목" )
        본문_텍스트 = 추출_본문(페이로드)
        회사, 직함 = parse_company_title(제목, 본문)
        이메일 타임스탬프 = parse_email_datetime(get_header(헤더, "날짜" ))

        항목 = {
            "메시지_id" : 메시지_id,
            "thread_id" : message.get( "threadId" ),
            "subject" : 주어,
            "회사" : 회사,
            "제목" : 제목,
            "status": "지원 완료",
            "email_date" : get_header(헤더, "날짜" ),
            "이메일_타임스탬프" : 이메일_타임스탬프,
            "synced_at" : datetime.now(timezone.utc).isoformat(),
            "스니펫" : message.get( "스니펫" , "" ),
            "normalized_company" : normalize_text(회사),
            "normalized_title" : normalize_text(제목),
        }
        new_entries.append(항목)

    new_entries를
 반환합니다


def merge_status_messages ( 기존: List [ Dict [ str , object ]], new_entries: List [ Dict [ str , object ]] ) -> List [ Dict [ str , object ]]:
 
    """새로운 메시지 항목을 기존 목록에 병합합니다(메시지 ID 기준)."""

    병합됨: Dict [ str , Dict [ str , object ]] = {}
    기존 항목 + 새 항목 에 대한 항목 :
        msg_id = str (item.get( "message_id" ))
        병합됨[msg_id] = 항목

    결과 = 목록 (병합된 값())
    result.sort(키= 람다 항목: item.get( "email_timestamp" ) 또는 item.get( "synced_at" ) 또는 "" , reverse= True )
 
    결과를
 반환하다


def main () -> 없음 :
 
    creds = load_credentials()
    신용이 아닌 경우 :
 
        print("⚠️ Gmail 인증 정보를 찾을 수 없어 지원 상태 업데이트를 건너뜁니다.")
        반품

    서비스 = 빌드( "gmail" , "v1" , 자격 증명=creds, 캐시_검색= False )

    데이터 = load_status_data()
    known_ids = { str (msg.get( "message_id" )) msg ​​in data.get( " messages " , []) msg.get( "message_id" )
 인 경우 }

    new_entries = fetch_new_messages(서비스, 알려진_ID)
    new_entries 가 아닌 경우 :
 
        print("ℹ️ 새로운 지원 완료 메일이 없어 상태 업데이트를 생략합니다.")
        반품

    데이터[ "메시지" ] = merge_status_messages(데이터.get( "메시지" , []), 새 항목)
    데이터[ "마지막_동기화_시간" ] = datetime.now(시간대.utc).isoformat()
    저장_상태_데이터(데이터)

    print(f"✅ {len(new_entries)}건의 지원 완료 메일을 상태 파일에 반영했습니다.")


if __name__ == "__main__" :   # pragma: no cover - 스크립트 진입점
    기본()
