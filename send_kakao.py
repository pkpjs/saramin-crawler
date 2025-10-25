def send_kakao_card(top10):
    global ACCESS_TOKEN

    if not ACCESS_TOKEN:
        ACCESS_TOKEN = refresh_access_token()

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"
    }

    contents = []
    for item in top10:
        contents.append({
            "title": item["title"],
            "description": item["desc"] or "채용 공고",
            "link": {
                "web_url": item["url"],
                "mobile_web_url": item["url"]
            }
        })

    template_object = {
        "object_type": "list",
        "header_title": f"오늘의 TOP {len(contents)} 채용",
        "header_link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL},
        "contents": contents,
        "buttons": [
            {
                "title": "전체 공고 보기",
                "link": {"web_url": PAGES_URL, "mobile_web_url": PAGES_URL}
            }
        ]
    }

    data = {
        "template_object": json.dumps(template_object, ensure_ascii=False)
    }

    response = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/send",
        headers=headers,
        data=data
    )

    result = response.json()
    print("카카오 응답:", result)

    if result.get("result_code") == 0:
        print("✅ 전송 성공!")
    else:
        print("❌ 메시지 전송 실패:", result)
