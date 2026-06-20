from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeRule:
    sector: str
    theme: str
    keywords: tuple[str, ...]
    us_keys: tuple[str, ...]
    issue_tags: tuple[str, ...]


THEME_RULES: tuple[ThemeRule, ...] = (
    ThemeRule("반도체", "AI/HBM 반도체", ("하이닉스", "삼성전자", "삼성전기", "한미반도체", "리노공업", "ISC", "피에스케이", "원익IPS", "테스", "HPSP", "유진테크", "동진쎄미켐", "솔브레인", "이오테크닉스", "주성엔지니어링"), ("nasdaq", "sox", "nvidia"), ("AI 서버", "HBM", "엔비디아")),
    ThemeRule("2차전지", "배터리/전기차", ("LG에너지솔루션", "삼성SDI", "포스코퓨처", "에코프로", "엘앤에프", "천보", "나노신소재", "코스모신소재", "SK아이이", "금양", "성일하이텍"), ("nasdaq", "tesla", "lithium"), ("전기차", "배터리", "리튬")),
    ThemeRule("바이오", "제약/바이오", ("바이오", "셀트리온", "삼성바이오", "알테오젠", "HLB", "유한양행", "한미약품", "종근당", "리가켐", "오스코텍", "에이비엘"), ("bio", "nasdaq"), ("FDA", "임상", "기술수출")),
    ThemeRule("방산", "방산/우주", ("한화에어로", "한국항공우주", "LIG넥스원", "현대로템", "풍산", "쎄트렉아이", "한화시스템", "인텔리안테크"), ("defense", "usdkrw"), ("수출계약", "국방비", "우주")),
    ThemeRule("조선", "조선/해운", ("HD현대중공업", "HD한국조선", "한화오션", "삼성중공업", "현대미포", "HJ중공업", "팬오션", "대한해운", "흥아해운"), ("oil", "usdkrw", "sp500"), ("수주", "운임", "LNG")),
    ThemeRule("전력", "전력/전선/원전", ("LS", "대한전선", "일진전기", "가온전선", "효성중공업", "HD현대일렉트릭", "두산에너빌리티", "한전기술", "한전KPS", "우진"), ("nasdaq", "sp500"), ("전력망", "AI 전력", "원전")),
    ThemeRule("로봇", "로봇/자동화", ("로보", "레인보우", "두산로보틱스", "에스피지", "뉴로메카", "유일로보틱스", "티로보틱스", "고영"), ("nasdaq", "nvidia"), ("휴머노이드", "공장자동화", "AI")),
    ThemeRule("자동차", "자동차/부품", ("현대차", "기아", "현대모비스", "HL만도", "성우하이텍", "화신", "에스엘", "명신산업", "SNT모티브"), ("tesla", "usdkrw", "sp500"), ("전기차", "환율", "수출")),
    ThemeRule("금융", "은행/증권/보험", ("KB금융", "신한지주", "하나금융", "우리금융", "기업은행", "삼성생명", "메리츠금융", "키움증권", "미래에셋증권", "한국금융지주"), ("finance", "sp500"), ("금리", "주주환원", "배당")),
    ThemeRule("화장품", "화장품/소비재", ("아모레", "LG생활건강", "코스맥스", "한국콜마", "클리오", "브이티", "실리콘투", "토니모리"), ("china", "usdkrw"), ("중국소비", "수출", "K뷰티")),
    ThemeRule("게임", "게임/콘텐츠", ("크래프톤", "엔씨소프트", "넷마블", "펄어비스", "카카오게임즈", "위메이드", "하이브", "JYP", "에스엠", "와이지"), ("nasdaq", "china"), ("신작", "콘텐츠", "플랫폼")),
)

ETF_WORDS = ("KODEX", "TIGER", "ACE", "RISE", "SOL", "KOSEF", "HANARO", "ETF", "ETN", "인버스", "레버리지", "선물")


def classify_stock(name: str) -> tuple[str, str, tuple[str, ...]]:
    text = name.upper()
    for rule in THEME_RULES:
        if any(keyword.upper() in text for keyword in rule.keywords):
            return rule.sector, rule.theme, rule.issue_tags
    return "기타", "개별 모멘텀", ()


def is_etf_like(name: str) -> bool:
    text = name.upper()
    return any(word.upper() in text for word in ETF_WORDS)


def theme_us_impact(theme: str, signals: dict[str, dict[str, float | str]]) -> float:
    rule = next((item for item in THEME_RULES if item.theme == theme), None)
    if not rule:
        return 0.0
    values = []
    for key in rule.us_keys:
        payload = signals.get(key) or {}
        change = payload.get("change_pct")
        if isinstance(change, (int, float)):
            values.append(float(change))
    if not values:
        return 0.0
    avg = sum(values) / len(values)
    return max(-8.0, min(8.0, avg * 2.0))
