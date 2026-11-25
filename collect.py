#!/usr/bin/env python3
"""대전 한눈에 — 구글 뉴스 RSS에서 대전 소식을 분야별로 모아 dashboard.html 을 만든다.

외부 라이브러리 없이 파이썬 기본 기능만 쓴다. 30분마다 자동 실행(launchd)되도록 설정되어 있다.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
DAYS = 7  # 최근 며칠치 기사를 모을지

# 분야별 검색어. 검색어를 늘리거나 빼려면 여기만 고치면 된다.
CATEGORIES = [
    ("incident", "사건·사고", ["대전 사고", "대전 화재", "대전 경찰 검거", "대전 사건", "대전 교통사고", "대전 숨져"]),
    ("politics", "시사·행정", ["대전시", "대전시의회", "대전시장", "대전 구청장", "대전교육청", "대전 국회의원"]),
    ("economy", "경제·개발", ["대전 경제", "대전 개발", "대전 아파트", "대전 트램", "대덕특구", "대전 기업"]),
    ("culture", "연예·문화", ["대전 연예인", "대전 공연", "대전 콘서트", "대전 촬영", "대전 전시", "대전예술의전당"]),
    ("event", "행사·축제", ["대전 축제", "대전 행사", "대전 박람회", "대전 페스티벌", "대전 0시 축제", "대전 개최"]),
    ("trend", "트렌드·핫플", ["대전 핫플", "대전 맛집", "성심당", "대전 빵", "대전 팝업", "대전 인기"]),
    ("sports", "스포츠", ["한화이글스", "대전하나시티즌", "대전 스포츠", "대전 체육"]),
]

# 대전 소식으로 인정할 낱말 (제목에 하나는 있어야 함)
LOCAL_WORDS = ["대전", "유성", "둔산", "대덕", "한화이글스", "한화 이글스", "하나시티즌", "성심당", "갑천", "보문산", "식장산", "엑스포", "KAIST", "카이스트", "충남대", "한밭"]
# '대전'이지만 지명이 아닌 경우
NOT_LOCAL = re.compile(r"(세계|할인|쇼핑|세일|특가|가격|요금|치킨|배달|라면|스마트폰|폰|e스포츠|게임|AI|반값|빅)\s?대전|대전쟁|대전환")

STOP = set("""대전 대전시 대전서 대전의 대전에서 대전시는 대전시가 대전시의 대전·세종·충남 대전·충남 대전·세종 충남 세종 충청 충북 지역 위해 위한 관련 대한 통해 진행 개최
있는 없는 한다 했다 된다 됐다 등 및 첫 더 또 수 것 중 제 년 월 일 오늘 내일 올해 내년 최근 이번 지난 오는 함께 대해 따라 대상 나서 나선다 가운데 기자 뉴스 속보 단독 종합 사진 영상 포토
기업 기관 점검 개방 불편 개막 앞마당 사회공헌 업무협약 협약 체결 개선 성공 활용 발표 공개 시작 결과 현장 방문 논의 협력 강조 당부 우수 최우수상 대상 확인 촉구 요구 제안 필요 문제 의견
마무리 최소화 출범 추진 여정 강화
위원 의원 시민 시장 구청장 교육감 추진 운영 지원 예정 실시 확대 강화 성료 마련 선정 참여 모집 기념 맞아 계획 사업 오후 오전 시간 이상 이하 만에 1 2 3 4 5 6 7 8 9 10""".split())

# 제목 낱말로 분야를 정한다 (위에서부터 먼저 맞는 분야). 아무것도 안 맞으면 검색한 분야를 쓴다.
RULES = [
    ("incident", r"(?<!적 )(?<!적)사고(?!력)|화재|불나|불이|숨져|숨진|사망|부상|다쳐|검거|구속|체포|입건|추락|충돌|추돌|음주운전|살인|폭행|흉기|사기|범죄|실종|기소|징역|유죄|무죄|선고|압수수색|학대|성범죄|추행|붕괴|누출|대피|구조됐|송치",
     r"예방|점검|조사|훈련|캠페인|대책|교육|강화|정비|화재안전|교통안전|안전관리|안전문화|우수|막는다|지원|협력|머리 맞|뭉쳤|한자리"),
    ("sports", r"한화이글스|한화 이글스|이글스|하나시티즌|K리그|프로야구|감독|선수|구단|경기|우승|ACLE|체육대회|마라톤|배구|농구", None),
    ("event", r"축제|행사|박람회|페스티벌|개막|개최|기념식|설명회|대회|체험|플리마켓|장터|불꽃|퍼레이드|0시", None),
    ("culture", r"공연|콘서트|연예|배우|가수|아이돌|드라마|영화|촬영|(?<!대)전시|뮤지컬|예술|미술관|박물관|무용|오케스트라|연극|문화원|도서", None),
    ("trend", r"성심당|맛집|핫플|팝업|빵|인기|화제|오픈런|줄서|명소|카페|여행|관광객|SNS|MZ", None),
    ("economy", r"경제|기업|아파트|분양|부동산|집값|트램|개발|투자|산업|일자리|창업|예산|특구|상권|소상공인|매출|공사|착공|준공|재개발", None),
    ("politics", r"시장|시의회|의원|구청장|교육감|국회|선거|정당|민주당|국민의힘|정책|조례|행정|시정|국정", None),
]
RULES = [(k, re.compile(a), re.compile(b) if b else None) for k, a, b in RULES]


def classify(title, fallback):
    for key, yes, no in RULES:
        if yes.search(title) and not (no and no.search(title)):
            return key
    # 사건·사고 검색에 걸렸지만 예방·점검 기사이면 시사·행정으로 보낸다
    return "politics" if fallback == "incident" else fallback


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def news_search(query):
    q = urllib.parse.quote(f"{query} when:{DAYS}d")
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        root = ET.fromstring(fetch(url))
    except Exception as e:  # 한 검색어가 실패해도 나머지는 계속
        print(f"  ! {query}: {e}", file=sys.stderr)
        return []
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)].strip()
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST)
        except Exception:
            continue
        out.append({"title": title, "source": source, "link": it.findtext("link") or "", "ts": int(dt.timestamp())})
    return out


def is_local(title):
    if NOT_LOCAL.search(title):
        return False
    return any(w in title for w in LOCAL_WORDS)


def norm(title):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", title)[:28]


def words(title):
    title = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", title)
    out = []
    for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", title):
        w = re.sub(r"(에서|으로|에게|까지|부터|이다|했다|한다|하는|하고|이며|에는|와의|과의|은|는|이|가|을|를|의|에|도|로|과|와|서)$", "", w)
        if len(w) >= 2 and w not in STOP and not w[0].isdigit():
            out.append(w)
    return out


def national_trends():
    try:
        root = ET.fromstring(fetch("https://trends.google.com/trending/rss?geo=KR"))
    except Exception as e:
        print(f"  ! 트렌드: {e}", file=sys.stderr)
        return []
    ns = "{https://trends.google.com/trending/rss}"
    out = []
    for it in list(root.iter("item"))[:15]:
        out.append({"title": it.findtext("title") or "", "traffic": it.findtext(ns + "approx_traffic") or ""})
    return out


def main():
    now = datetime.now(KST)
    cutoff = int((now - timedelta(days=DAYS)).timestamp())
    jobs = [(key, q) for key, _, qs in CATEGORIES for q in qs]
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda j: (j[0], news_search(j[1])), jobs))
        trends_future = ex.submit(national_trends)
        trends = trends_future.result()

    seen = {}
    for key, items in results:
        for a in items:
            if a["ts"] < cutoff or not is_local(a["title"]):
                continue
            k = norm(a["title"])
            if k not in seen:
                seen[k] = dict(a, cat=classify(a["title"], key))

    # 비슷한 제목(같은 보도자료를 여러 신문이 쓴 경우)은 가장 먼저 나온 하나만 남기고 "같은 소식 N건"으로 센다
    articles = []
    for a in sorted(seen.values(), key=lambda a: a["ts"]):
        ws = set(words(a["title"]))
        dup = None
        for b in articles[-400:]:
            if ws and b["_w"] and len(ws & b["_w"]) / len(ws | b["_w"]) >= 0.45:
                dup = b
                break
        if dup:
            dup["more"] = dup.get("more", 0) + 1
            dup["ts"] = max(dup["ts"], a["ts"])
        else:
            a["_w"] = ws
            articles.append(a)
    for a in articles:
        del a["_w"]
    articles.sort(key=lambda a: -a["ts"])

    # 대전 키워드: 최근 24시간 제목에 많이 나온 낱말.
    # 날마다 낱말 수를 history.json 에 남겨 두고, 지난 며칠 평균의 2배 이상이면 ▲(급상승)로 표시한다.
    day_ago = int((now - timedelta(hours=24)).timestamp())
    recent = Counter()
    for a in articles:
        if a["ts"] >= day_ago:
            recent.update(set(words(a["title"])))
    hist_path = HERE / "history.json"
    try:
        hist = json.loads(hist_path.read_text(encoding="utf-8"))
    except Exception:
        hist = {}
    today = now.strftime("%Y-%m-%d")
    hist[today] = dict(recent.most_common(300))
    hist = {d: hist[d] for d in sorted(hist)[-15:]}
    hist_path.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
    past = [hist[d] for d in sorted(hist) if d != today][-7:]
    keywords = []
    for w, n in recent.most_common(24):
        if n < 2:
            continue
        rise = False
        if len(past) >= 3:
            avg = sum(p.get(w, 0) for p in past) / len(past)
            rise = n >= 4 and n >= 2 * max(avg, 1)
        keywords.append({"word": w, "count": n, "rise": rise})

    data = {
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "updatedTs": int(now.timestamp()),
        "days": DAYS,
        "categories": [{"key": k, "name": n} for k, n, _ in CATEGORIES],
        "articles": articles,
        "keywords": keywords,
        "trends": trends,
    }
    (HERE / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tpl = (HERE / "template.html").read_text(encoding="utf-8")
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    (HERE / "dashboard.html").write_text(tpl.replace("/*__DATA__*/null", blob), encoding="utf-8")
    print(f"{now:%m-%d %H:%M} 기사 {len(articles)}건, 키워드 {len(keywords)}개, 전국 트렌드 {len(trends)}개")


if __name__ == "__main__":
    main()
