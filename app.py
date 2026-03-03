"""
Klear K-Beauty Market Intelligence — Flask 서버
VS Code 터미널에서: python app.py
브라우저: http://localhost:5000
"""

from flask import Flask, jsonify, request, render_template
from datetime import datetime
import json, os, pathlib, concurrent.futures as _cf, threading, uuid

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Google GenAI (Imagen 3 이미지 + Veo 2 영상)
try:
    from google import genai
    from google.genai import types as gtypes
    GOOGLE_GENAI_OK = True
except ImportError:
    genai = None
    gtypes = None
    GOOGLE_GENAI_OK = False

app = Flask(__name__, static_folder="static", template_folder="templates")

# ─── 백그라운드 태스크 매니저 ────────────────────────────────────────────────
_tasks: dict = {}  # { task_id: {"status": "processing"|"done"|"error", "result"?: str, "error"?: str} }


# ═══════════════════════════════════════════════════════════════════════════
# DeepL 번역 헬퍼
# ═══════════════════════════════════════════════════════════════════════════

def deepl_to_english(text: str) -> str:
    if not text:
        return text
    has_korean = any('\uAC00' <= c <= '\uD7A3' or '\u1100' <= c <= '\u11FF' for c in text)
    if not has_korean:
        return text
    DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY", "")
    if not DEEPL_API_KEY:
        return text
    try:
        import requests as _req
        base_url = (
            "https://api-free.deepl.com/v2/translate"
            if DEEPL_API_KEY.endswith(":fx")
            else "https://api.deepl.com/v2/translate"
        )
        resp = _req.post(
            base_url,
            headers={"Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}"},
            json={"text": [text], "target_lang": "EN-US"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()["translations"][0]["text"]
    except Exception:
        pass
    return text


# ═══════════════════════════════════════════════════════════════════════════
# 제품 데이터 로더
# ═══════════════════════════════════════════════════════════════════════════

DATA_DIR = pathlib.Path(__file__).parent / "data"

def load_product_info(product_name: str) -> dict:
    if not product_name:
        return {}
    name_lower = product_name.strip().lower()
    db_path = DATA_DIR / "products.db"
    if db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute(
                "SELECT * FROM products WHERE LOWER(name) LIKE ? LIMIT 1",
                (f"%{name_lower}%",)
            )
            row = cursor.fetchone()
            if row:
                cols = [d[0] for d in cursor.description]
                conn.close()
                return dict(zip(cols, row)) | {"source": "db"}
            conn.close()
        except Exception:
            pass
    txt_path = DATA_DIR / "products.txt"
    if txt_path.exists():
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                for line in f:
                    if name_lower in line.lower():
                        parts = [p.strip() for p in line.split("|")]
                        if len(parts) >= 2:
                            return {"name": parts[0], "description": parts[1], "source": "txt"}
        except Exception:
            pass
    return {}


def build_product_prompt_addon(product_info: dict) -> str:
    if not product_info:
        return ""
    parts = []
    for key in ["description", "ingredients", "texture", "color", "packaging"]:
        val = product_info.get(key, "")
        if val:
            parts.append(str(val)[:80])
    return ", ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# 시나리오 / 멀티플랫폼 / 스토리보드 / 이미지 / 영상 상수
# ═══════════════════════════════════════════════════════════════════════════

SCENARIO_PROMPTS = {
    "reels_15s": {"system": (
        "당신은 K-뷰티 인스타그램 릴스 전문 크리에이티브 디렉터입니다.\n"
        "15초 이내 숏폼 릴스 콘텐츠 스크립트를 다음 구조로 반드시 작성하세요:\n\n"
        "## 🎬 릴스 스크립트 (15초)\n"
        "- **[0-3초] 훅**: 첫 3초 안에 스크롤을 멈추는 강렬한 오프닝\n"
        "- **[3-10초] 메인 씬**: 제품 사용 장면 + 핵심 소구 포인트\n"
        "- **[10-15초] CTA**: 구매/팔로우 유도 문구\n\n"
        "## 📸 촬영 가이드\n## ✂️ 편집 지시\n## 🏷️ 해시태그 (7개)"
    )},
    "before_after": {"system": (
        "당신은 TikTok Before/After 챌린지 콘텐츠 전문가입니다.\n"
        "## ⚡ 훅 — 첫 2초\n## 📸 BEFORE 씬\n## ✨ AFTER 씬\n"
        "## 🎵 영상 구성\n## 📣 CTA + 해시태그 (5개)"
    )},
    "shorts_3min": {"system": (
        "당신은 YouTube Shorts K-뷰티 루틴 영상 전문 기획자입니다.\n"
        "## 📌 영상 제목 (3가지)\n## ⏱️ 타임코드별 구성\n"
        "## 🎙️ 나레이션 스크립트\n## 🎬 촬영 & 편집 팁\n## 🏷️ YouTube 태그 (8개)"
    )},
    "ugc_campaign": {"system": (
        "당신은 K-뷰티 브랜드 UGC 캠페인 전략 전문가입니다.\n"
        "## 🏆 캠페인 개요\n## 📋 참여 방법\n## 🎁 인센티브 구조\n"
        "## 📢 캠페인 메시지\n## 📊 성과 측정 KPI\n## 🏷️ 해시태그 (5개)"
    )},
}

SCENARIO_EXTRA_LABEL = {
    "grwm": "GRWM 스타일", "transition": "트랜지션 편집 스타일",
    "voiceover": "보이스오버 나레이션", "text_only": "텍스트+클로즈업",
    "15s": "15초", "30s": "30초", "60s": "60초",
    "3step": "3단계 루틴", "5step": "5단계 루틴", "7step": "7단계 루틴",
    "gift": "제품 증정", "discount": "할인 쿠폰", "feature": "공식 피처링", "cash": "캐시백",
}

PLATFORM_SYSTEM_PROMPTS = {
    "instagram": (
        "당신은 K-뷰티 인스타그램 전문 콘텐츠 마케터입니다.\n"
        "1. 🎣 후킹 제목 A/B 2가지\n2. 📝 메인 본문 (이모지 포함)\n3. 🏷️ 해시태그 5~7개"
    ),
    "tiktok": (
        "당신은 TikTok 바이럴 콘텐츠 전문가입니다.\n"
        "1. ⚡ 훅 (첫 3초)\n2. 🎬 영상 스크립트\n3. 🎵 BGM 추천\n4. 🏷️ 해시태그 5개"
    ),
    "youtube": (
        "당신은 YouTube Shorts 최적화 전문가입니다.\n"
        "1. 📌 SEO 제목\n2. 📝 영상 설명\n3. 🎬 쇼츠 스크립트\n4. 🏷️ 태그 5~8개"
    ),
    "twitter": (
        "당신은 X(Twitter) 바이럴 카피라이터입니다.\n"
        "1. 🐦 첫 트윗\n2. 🧵 스레드 트윗 2~3개\n3. 💡 CTA 트윗\n4. 🏷️ 해시태그 2~3개"
    ),
    "blog": (
        "당신은 K-뷰티 블로그 에디터입니다.\n"
        "1. 📰 SEO 제목\n2. 🔍 메타 설명\n3. 📄 본문 요약\n4. 🏷️ 태그 5개"
    ),
}

GOAL_LABELS = {
    "awareness": "브랜드 인지도 확산", "conversion": "구매 전환 유도",
    "engagement": "팔로워 인게이지먼트", "ugc": "UGC 챌린지",
    "launch": "신제품 런칭 홍보",
}

AD_FORMAT_LABELS = {
    "15sec": "15초 숏폼", "30sec": "30초 숏폼 광고",
    "60sec": "60초 유튜브 광고", "tiktok_trend": "TikTok 트렌드 챌린지",
}

TONE_LABELS = {
    "clean_minimal": "Clean & Minimal", "trendy_fun": "Trendy & Fun",
    "luxe_premium": "Luxe & Premium", "science_trust": "Science & Trust",
    "emotional_story": "Emotional Story",
}

HF_IMAGE_SIZE = {
    "instagram": (1024, 1024), "tiktok": (576, 1024),
    "youtube": (1024, 576), "twitter": (1024, 576), "blog": (1024, 576),
}

HF_STYLE_HINT = {
    "instagram": "clean white studio background, square composition, K-beauty editorial aesthetic, soft natural lighting",
    "tiktok": "vertical composition, bold vibrant colors, Gen-Z Korean beauty aesthetic",
    "youtube": "wide horizontal composition, dramatic lighting, professional beauty editorial",
    "twitter": "wide horizontal banner, clean modern marketing visual, K-beauty brand aesthetic",
    "blog": "wide hero image, editorial magazine style, natural lifestyle Korean skincare aesthetic",
}

VEO_ASPECT = {
    "instagram": "9:16", "tiktok": "9:16",
    "youtube": "16:9", "twitter": "16:9", "blog": "16:9",
}


def _vertex_auth_headers(credentials_file: str) -> dict:
    """Service Account JSON → Bearer 토큰"""
    import json as _json, time, urllib.request, base64 as _b64
    with open(credentials_file, "r") as f:
        sa = _json.load(f)
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend
    now = int(time.time())
    header  = _b64.urlsafe_b64encode(_json.dumps({"alg":"RS256","typ":"JWT"}).encode()).rstrip(b"=")
    payload = _b64.urlsafe_b64encode(_json.dumps({
        "iss": sa["client_email"], "sub": sa["client_email"],
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now, "exp": now + 3600,
        "scope": "https://www.googleapis.com/auth/cloud-platform",
    }).encode()).rstrip(b"=")
    private_key = serialization.load_pem_private_key(
        sa["private_key"].encode(), password=None, backend=default_backend()
    )
    sign_input = header + b"." + payload
    signature  = private_key.sign(sign_input, padding.PKCS1v15(), hashes.SHA256())
    jwt_token  = (sign_input + b"." + _b64.urlsafe_b64encode(signature).rstrip(b"=")).decode()
    body = f"grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion={jwt_token}".encode()
    req  = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        token_data = _json.loads(resp.read())
    return {
        "Authorization": f"Bearer {token_data['access_token']}",
        "Content-Type":  "application/json",
    }


# ─────────────────────────── 샘플 데이터 ───────────────────────────────────
def get_intelligence_data():
    return {
        "trend_count": 247, "trend_change": "+18.3%",
        "trends": [
            {"rank":1,"keyword":"Snail Mucin Routine",   "source":"Reddit",    "volume":"28k"},
            {"rank":2,"keyword":"Glass Skin Tutorial",   "source":"TikTok",    "volume":"19k"},
            {"rank":3,"keyword":"COSRX vs Klear compare","source":"Instagram", "volume":"7.4k"},
            {"rank":4,"keyword":"Ceramide Moisturizer",  "source":"Reddit",    "volume":"5.1k"},
            {"rank":5,"keyword":"Korean SPF Routine",    "source":"TikTok",    "volume":"4.8k"},
        ],
        "hot_keywords":    ["Snail Mucin","Glass Skin","Centella","Double Cleanse","K-Beauty Haul","COSRX","Skin Barrier"],
        "rising_keywords": ["Klear Serum","Bio-Wellness","Ceramide Cream","Peptide Ampoule","Fermented Essence","Hydrogel Patch"],
        "platform_stats":  {"TikTok":48,"Instagram":31,"Reddit":21},
        "sparkline_7d":    [30,45,38,55,72,60,88],
        "updated_at": datetime.now().strftime("%H:%M KST"),
    }

def get_outreach_data():
    return {
        "active_influencers":83, "mail_sent":67, "mail_total":100,
        "open_rate":44, "reply_rate":21,
        "influencers":[
            {"initials":"SR","name":"@skincarebyrose",  "followers":"234K","category":"Beauty",   "status":"open",   "status_label":"열람"},
            {"initials":"JL","name":"@jenloves_kbeauty","followers":"89K", "category":"Lifestyle","status":"replied","status_label":"회신 ✓"},
            {"initials":"MT","name":"@makeupwithmia",   "followers":"1.2M","category":"Makeup",   "status":"pending","status_label":"대기중"},
            {"initials":"CS","name":"@cleanskinsophia", "followers":"312K","category":"Skincare", "status":"sent",   "status_label":"발송됨"},
        ],
        "response_rate":31, "response_change":"-2.1%p",
        "updated_at": datetime.now().strftime("%H:%M KST"),
    }

def get_content_data():
    return {
        "content_count":136, "content_change":"+34.1%",
        "ab_test":{
            "variant_a":{"headline":"피부 장벽을 되살리는 클리어 루틴","ctr":3.2},
            "variant_b":{"headline":"3일 만에 달라진 결: Klear 후기",  "ctr":5.8,"winner":True},
        },
        "scenarios":[
            {"platform":"instagram","icon":"📸","title":"유리 피부 연출 GRWM — 15초 릴스",      "score":92},
            {"platform":"tiktok",   "icon":"▶", "title":"Before/After 세럼 챌린지 포맷",       "score":88},
            {"platform":"youtube",  "icon":"▷", "title":"K-Beauty 루틴 풀영상 (3분 쇼츠)",      "score":79},
            {"platform":"instagram","icon":"📸","title":"#KlearChallenge UGC 캠페인 시나리오",   "score":74},
        ],
        "updated_at": datetime.now().strftime("%H:%M KST"),
    }

# ─────────────────────────── 기본 API ──────────────────────────────────────
@app.route("/api/intelligence")
def api_intelligence(): return jsonify(get_intelligence_data())

@app.route("/api/outreach")
def api_outreach(): return jsonify(get_outreach_data())

@app.route("/api/content")
def api_content(): return jsonify(get_content_data())

@app.route("/api/all")
def api_all():
    return jsonify({"intelligence":get_intelligence_data(),"outreach":get_outreach_data(),"content":get_content_data()})


# ═══════════════════════════════════════════════════════════════════════════
# 카테고리별 SerpApi 검색 키워드 정의
# ─────────────────────────────────────────────────────────────────────────
# 4개 카테고리 모두 SerpApi → OpenAI 방식으로 통일
# ═══════════════════════════════════════════════════════════════════════════

CATEGORY_SEARCH_KEYWORDS = {
    "진입 장벽": "beauty cosmetics market entry barriers distribution competition local brands",
    "수출 규제": "cosmetics import regulation FDA requirements labeling banned ingredients certification",
    "문화":      "consumer culture beauty preference lifestyle local trend",
    "소비자 트렌드": "consumer trend demand skincare market growth popular 2026",
}

# ── 공통 유틸: 서킷 브레이커(경량) + 전략 패턴 ────────────────────────────

def _run_with_timeout(fn, timeout: int = 90):
    """외부 블로킹 API(Apify 등)에 타임아웃을 적용하는 경량 서킷 브레이커."""
    with _cf.ThreadPoolExecutor(max_workers=1) as executor:
        try:
            return executor.submit(fn).result(timeout=timeout)
        except _cf.TimeoutError:
            raise TimeoutError(f"외부 API 응답 없음 ({timeout}초 초과)")

def _call_openai(api_key: str, model: str, messages: list,
                 temperature: float = 0.3, max_tokens: int = 1200) -> str:
    """
    전략 패턴(Strategy Pattern) 기반 OpenAI 통합 호출.
    - API 키 미설정 시 ValueError 발생 → 각 라우트가 데모 텍스트로 대응
    - timeout=30.0 으로 응답 지연 SPOF 차단
    """
    if not api_key:
        raise ValueError("OPENAI_API_KEY 미설정")
    from openai import OpenAI
    client = OpenAI(api_key=api_key, timeout=120.0)
    res = client.chat.completions.create(
        model=model, messages=messages,
        temperature=temperature, max_tokens=max_tokens,
    )
    return res.choices[0].message.content

# ─── SerpApi 검색 (카테고리 통합) ──────────────────────────────────────────
def _fetch_serp_data_for_categories(
    country: str, item: str, categories: list, api_key: str
) -> tuple[str, list]:
    """
    선택된 모든 카테고리에 대해 SerpApi 구글 검색을 수행합니다.
    카테고리별로 최적화된 검색 쿼리를 사용해 관련도 높은 결과를 가져옵니다.
    Returns: (컨텍스트 텍스트 블록, raw_sources 리스트)
    """
    all_text    = ""
    all_sources = []

    if not api_key:
        all_text = "[데모] SERPAPI_KEY 미설정 — 실제 데이터를 가져오려면 .env에 SERPAPI_KEY를 입력하세요.\n"
        all_sources = [{"index": 1, "title": "SERPAPI_KEY 미설정", "snippet": "데모 모드", "link": "", "category": "전체"}]
        return all_text, all_sources

    import urllib.request, urllib.parse

    item_part = f" {item}" if item else ""

    for category in categories:
        keywords = CATEGORY_SEARCH_KEYWORDS.get(category, "beauty cosmetics")
        query    = f"{country}{item_part} {keywords} 2026"

        try:
            params = urllib.parse.urlencode({
                "q":       query,
                "api_key": api_key,
                "num":     5,
                "hl":      "en",
                "gl":      "us",
            })
            with urllib.request.urlopen(
                f"https://serpapi.com/search.json?{params}", timeout=10
            ) as resp:
                results = json.loads(resp.read().decode()).get("organic_results", [])[:5]

            if results:
                all_text += f"\n── [{category}] 검색 결과 (쿼리: {query}) ──\n"
                for i, r in enumerate(results):
                    all_sources.append({
                        "index":    len(all_sources) + 1,
                        "title":    r.get("title", ""),
                        "snippet":  r.get("snippet", ""),
                        "link":     r.get("link", ""),
                        "category": category,
                    })
                    all_text += f"[{category}-{i+1}] {r.get('title','')}\n{r.get('snippet','')}\n\n"
            else:
                all_text += f"[{category}] 검색 결과 없음\n\n"

        except Exception as e:
            all_text    += f"[{category}] SerpApi 오류: {e}\n\n"
            all_sources.append({
                "index":    len(all_sources) + 1,
                "title":    f"SerpApi 오류 ({category})",
                "snippet":  str(e),
                "link":     "",
                "category": category,
            })

    return all_text, all_sources


# ─── 카테고리별 AI 분석 지침 ────────────────────────────────────────────────
_CATEGORY_PROMPT = {
    "진입 장벽": (
        "## 🚧 시장 진입 장벽\n"
        "- 유통 채널 구조, 현지 경쟁 브랜드, 가격 민감도, 브랜드 신뢰도 장벽을 분석하세요.\n"
        "- 검색 데이터에 근거해 구체적인 장벽 요소를 서술하세요.\n"
        "- 🚨 출력 시 각 항목을 `- **[소제목]** 설명` 형태로 작성하되, 소제목은 '분석 항목:'처럼 고정 라벨을 쓰지 말고 내용에 맞는 실제 항목명(예: **유통 채널 구조**, **가격 민감도** 등)을 사용하세요."
    ),
    "수출 규제": (
        "## 📋 수출 규제 및 법적 요건\n"
        "- 화장품 수입 규정, 인증 요구사항(예: FDA 등록), 금지 성분, 라벨링 규정을 분석하세요.\n"
        "- 검색 데이터에 근거한 내용만 서술하고, 불확실한 규정은 '추가 확인 필요'로 표기하세요.\n"
        "- 🚨 대상 국가가 미국처럼 주(State) 단위로 별도 규제가 있는 경우, 연방 규제와 주요 주별 규제(예: 캘리포니아 주 Prop 65 등)를 명확히 분리하여 서술하세요.\n"
        "- 🚨 출력 시 각 항목을 `- **[규제명]** 설명` 형태로 작성하되, 소제목은 '규제 항목명:'처럼 고정 라벨을 쓰지 말고 실제 규제명(예: **FDA 등록 요건**, **라벨링 규정** 등)을 사용하세요."
    ),
    "문화": (
        "## 🌏 문화적 특성\n"
        "- 현지 뷰티 문화, 피부 관리 습관, 미적 기준, K-뷰티 인식을 분석하세요.\n"
        "- 문화적 선호도와 금기 사항도 포함하세요.\n"
        "- 🚨 출력 시 각 항목을 `- **[특성명]** 설명` 형태로 작성하되, 소제목은 '문화적 특징:'처럼 고정 라벨을 쓰지 말고 실제 특성명(예: **현지 뷰티 트렌드**, **K-뷰티 인식** 등)을 사용하세요."
    ),
    "소비자 트렌드": (
        "## 📈 소비자 트렌드 및 전략적 시사점\n"
        "- 소비 패턴, 인기 성분/제품, SNS 트렌드, 구매 채널 선호도를 분석하세요.\n"
        "- Klear 브랜드가 이 트렌드를 활용할 수 있는 실질적 전략도 제시하세요.\n"
        "- 🚨 출력 시 각 항목을 `- **[트렌드명]** 설명` 형태로 작성하되, 소제목은 '트렌드 요인:'처럼 고정 라벨을 쓰지 말고 실제 트렌드명(예: **글래스 스킨 열풍**, **성분 중심 소비** 등)을 사용하세요."
    ),
}


# ═══════════════════════════════════════════════════════════════════════════
# 해시태그 Stop-word 필터 (Apify 검색 미끼 태그 + 범용 태그 제외)
# ═══════════════════════════════════════════════════════════════════════════

EXCLUDED_TAGS = [
    # Apify 검색 미끼 태그 (수집용으로만 쓰고 결과엔 노출 안 함)
    'sephorahaul', 'targetbeauty', 'ultabeauty',
    'skincareroutine', 'skincaretips', 'glowingskin', 'healthyskin',
    'sunscreen', 'spf',  # 수집 시드 — 결과엔 뻔해서 제외
    # 범용 뷰티 태그 (너무 넓어서 마케팅 가치 없음)
    'beauty', 'skincare', 'makeup', 'cosmetics',
    # TikTok 알고리즘 태그 (트렌드와 무관)
    'fyp', 'foryou', 'foryoupage', 'viral', 'trending',
]


# ═══════════════════════════════════════════════════════════════════════════
# 플랫폼별 맞춤형 AI 분석 프롬프트 딕셔너리
# ═══════════════════════════════════════════════════════════════════════════

PLATFORM_PROMPTS = {

    # ── TikTok: Z세대 숏폼 바이럴 마케터 ───────────────────────────────────
    'TikTok': """너는 Z세대를 타겟으로 한 숏폼 뷰티 바이럴 마케터이자 트렌드 애널리스트야.
다음은 미국 TikTok 뷰티 영상에서 실시간으로 수집된 해시태그 빈도 데이터와 영상 캡션이야.

[해시태그 빈도 데이터]
{tags_str}

[영상 캡션 샘플]
{sample_txt}

[수행 작업 및 분석 가이드라인]
제공된 데이터를 심층 분석하여, 현재 미국 TikTok에서 Z세대가 가장 열광하는 **핵심 뷰티 트렌드 키워드 TOP {limit}**을 도출해.
단, 다음의 깐깐한 규칙을 무조건 엄수해.

1. 절대적 객관성 유지 (할루시네이션 금지):
   - 오직 '제공된 데이터' 내에서만 빈도수가 높고 문맥상 중요도가 큰 키워드를 추출할 것.
   - 데이터에 없는 트렌드를 지어내거나 사전 지식을 섞지 말 것.

2. 키워드 그룹핑 (의미망 분석):
   - 비슷한 의미의 단어(예: Sunscreen/SPF/Sunblock)는 가장 대표적인 하나로 통합할 것.

3. TikTok 특화 분석 관점:
   - 즉각적인 시각적 효과(Before/After), 챌린지 포맷, 가성비, 빠른 변화를 보여주는 키워드에 집중할 것.
   - '#GRWM', '#SkincareTok', 특정 성분 챌린지 등 TikTok에서만 바이럴되는 숏폼 포맷 키워드를 발굴할 것.
   - skin/face/good 같은 범용 단어는 무조건 배제할 것.

[출력 형식]
- 마크다운 기호 없이 오직 순수한 JSON 배열만 출력할 것.
- 'keyword'는 원문 영어 그대로, 'summary'는 한국어로 2~3줄 상세 분석.

[
  {{
    "keyword": "영어 키워드명",
    "mentions": 카운트숫자,
    "summary": "이 키워드가 TikTok Z세대 사이에서 왜 핫한지, 숏폼 바이럴 마케팅 관점의 시사점을 한국어 2~3줄로 분석"
  }}
]""",

    # ── Instagram: 비주얼·인플루언서 마케팅 전문가 ─────────────────────────
    'Instagram': """너는 밀레니얼/Z세대를 타겟으로 한 비주얼 및 인플루언서 마케팅 전문가이자 K-뷰티 트렌드 애널리스트야.
다음은 미국 Instagram 뷰티 게시물에서 실시간으로 수집된 해시태그 빈도 데이터와 캡션이야.

[해시태그 빈도 데이터]
{tags_str}

[게시물 캡션 샘플]
{sample_txt}

[수행 작업 및 분석 가이드라인]
제공된 데이터를 심층 분석하여, 현재 미국 Instagram에서 가장 영향력 있는 **핵심 뷰티 트렌드 키워드 TOP {limit}**을 도출해.
단, 다음의 깐깐한 규칙을 무조건 엄수해.

1. 절대적 객관성 유지 (할루시네이션 금지):
   - 오직 '제공된 데이터' 내에서만 빈도수가 높고 문맥상 중요도가 큰 키워드를 추출할 것.
   - 데이터에 없는 트렌드를 지어내거나 사전 지식을 섞지 말 것.

2. 키워드 그룹핑 (의미망 분석):
   - 비슷한 의미의 단어(예: GlassSkin/GlowySkin/DewySkin)는 가장 대표적인 하나로 통합할 것.

3. Instagram 특화 분석 관점:
   - 라이프스타일 결합, 패키지 감성, 인플루언서 추천템, GRWM(Get Ready With Me) 포맷 등 감각적이고 미적인 키워드에 집중할 것.
   - 릴스 바이럴, 언박싱, 플랫레이 같은 Instagram 고유 콘텐츠 포맷과 연결된 키워드를 발굴할 것.
   - skin/face/good 같은 범용 단어는 무조건 배제할 것.

[출력 형식]
- 마크다운 기호 없이 오직 순수한 JSON 배열만 출력할 것.
- 'keyword'는 원문 영어 그대로, 'summary'는 한국어로 2~3줄 상세 분석.

[
  {{
    "keyword": "영어 키워드명",
    "mentions": 카운트숫자,
    "summary": "이 키워드가 Instagram 비주얼/인플루언서 마케팅 관점에서 왜 핫한지, K-뷰티 전략 시사점을 한국어 2~3줄로 분석"
  }}
]""",

    # ── YouTube: 뷰티 심층 리뷰어 및 성분 분석가 ───────────────────────────
    'YouTube': """너는 뷰티 심층 리뷰어이자 성분 분석 전문가야. 꼼꼼한 소비자와 K-뷰티 마니아 집단을 대상으로 신뢰도 높은 콘텐츠 트렌드를 분석해.
다음은 미국 YouTube 뷰티 콘텐츠에서 실시간으로 수집된 키워드 빈도 데이터와 영상 제목/설명이야.

[키워드 빈도 데이터]
{tags_str}

[영상 제목/설명 샘플]
{sample_txt}

[수행 작업 및 분석 가이드라인]
제공된 데이터를 심층 분석하여, 현재 미국 YouTube 뷰티 시청자들이 가장 신뢰하고 탐색하는 **핵심 뷰티 트렌드 키워드 TOP {limit}**을 도출해.
단, 다음의 깐깐한 규칙을 무조건 엄수해.

1. 절대적 객관성 유지 (할루시네이션 금지):
   - 오직 '제공된 데이터' 내에서만 빈도수가 높고 문맥상 중요도가 큰 키워드를 추출할 것.
   - 데이터에 없는 트렌드를 지어내거나 사전 지식을 섞지 말 것.

2. 키워드 그룹핑 (의미망 분석):
   - 비슷한 의미의 단어(예: Tutorial/HowTo/Guide)는 가장 대표적인 하나로 통합할 것.

3. YouTube 특화 분석 관점:
   - 꼼꼼한 튜토리얼, 장기 사용 후기(1달/3달), 성분 심층 분석(펩타이드/세라마이드/레티놀 등), 피부 타입별 맞춤 루틴 등 신뢰도와 정보 깊이를 중시하는 키워드에 집중할 것.
   - '더마톨로지스트 추천', '임상 테스트', '성분 비교' 같은 전문성·신뢰성 관련 키워드를 발굴할 것.
   - skin/face/good 같은 범용 단어는 무조건 배제할 것.

[출력 형식]
- 마크다운 기호 없이 오직 순수한 JSON 배열만 출력할 것.
- 'keyword'는 원문 영어 그대로, 'summary'는 한국어로 2~3줄 상세 분석.

[
  {{
    "keyword": "영어 키워드명",
    "mentions": 카운트숫자,
    "summary": "이 키워드가 YouTube 심층 리뷰/성분 분석 관점에서 왜 중요한지, K-뷰티 콘텐츠 전략 시사점을 한국어 2~3줄로 분석"
  }}
]""",

    # ── Reddit: 깐깐한 스킨케어 커뮤니티 데이터 애널리스트 ─────────────────
    'Reddit': """너는 K-뷰티 및 글로벌 스킨케어 시장 트렌드를 분석하는 수석 데이터 애널리스트이자 마케팅 전략가야.
다음은 레딧(Reddit) K-뷰티/스킨케어 커뮤니티에서 실시간으로 수집된 유저들의 날것(Raw) 게시글 데이터야.

[게시글 데이터]
{combined_text}

[수행 작업 및 분석 가이드라인]
제공된 데이터를 심층 분석하여, 현재 유저들이 가장 열광하거나 고민하고 있는 **핵심 뷰티 트렌드 키워드 TOP {limit}**을 도출해.
단, 다음의 깐깐한 규칙을 무조건 엄수해서 분석해야 해.

1. 절대적 객관성 유지 (할루시네이션 금지):
   - 내가 특정 예시(정답)를 주지 않더라도, 오직 '제공된 텍스트' 내에서만 언급 빈도수가 높고 문맥상 중요도가 큰 단어를 스스로 추출할 것.
   - 데이터에 없는 트렌드를 지어내거나 너의 사전 지식을 섞지 말 것.

2. 키워드 그룹핑 (의미망 분석):
   - 비슷한 의미를 가진 단어들(예: Sunscreen, SPF, Sunblock / Hydration, Moisturizing 등)은 문맥을 파악하여 하나의 가장 대표적인 키워드로 통합하여 순위를 매길 것.

3. Reddit 특화 분석 관점 (깐깐한 데이터 애널리스트):
   - 과장 광고를 혐오하고 진짜 성분과 효능에 집착하는 Reddit 유저 특성을 반영할 것.
   - Skin, Face, Good 같은 뻔하고 광범위한 단어는 무조건 배제할 것.
   - 백탁 현상(White cast), 발림성(Texture), 자외선 차단 성능, 특정 성분 논란 등 아주 구체적이고 솔직한 커뮤니티 고민 키워드를 발굴할 것.
   - 마케팅 전략(특히 선세럼 등 신제품 기획)에 즉시 활용할 수 있도록 엣지 있는 키워드를 도출할 것.

[출력 형식]
- 마크다운 기호 없이 오직 순수한 JSON 배열만 출력할 것.
- 🚨 중요: 글로벌 마케팅 활용을 위해 'keyword'는 원문에서 추출한 **정확한 영어 명사/형용사**로 유지하고, 'summary' 내용은 우리 마케팅 팀원들이 읽기 편하게 **자연스러운 한국어로 상세하게(2~3줄)** 요약할 것.

[
  {{
    "keyword": "영어 키워드명 (예: White cast)",
    "mentions": 카운트숫자,
    "summary": "Reddit 유저들이 이 키워드와 관련하여 구체적으로 어떤 불편함을 겪고 있는지, 혹은 어떤 효과에 열광하고 있는지 한국어로 상세히 분석한 요약"
  }}
]""",
}


# ─── 실시간 트렌드 키워드 분석 (/api/trend-keywords) ───────────────────────
@app.route("/api/trend-keywords", methods=["POST"])
def api_trend_keywords():
    try:
        data     = request.get_json(force=True)
        platform = data.get("platform", "전체").strip()
        category = data.get("category", "K-뷰티 스킨케어").strip()

        SERP_API_KEY   = os.environ.get("SERPAPI_KEY", "")
        OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

        limit = 10  # Instagram / YouTube 개별 플랫폼은 TOP 10

        # 1. SerpApi 검색
        search_query        = f"K-beauty {category} trend {platform} 2026 popular keywords hashtags"
        raw_sources         = []
        search_results_text = ""

        if SERP_API_KEY:
            try:
                import urllib.request, urllib.parse
                params = urllib.parse.urlencode({
                    "q": search_query, "api_key": SERP_API_KEY,
                    "num": 5, "hl": "en", "gl": "us"
                })
                with urllib.request.urlopen(
                    f"https://serpapi.com/search.json?{params}", timeout=10
                ) as resp:
                    serp_data = json.loads(resp.read().decode())
                for i, r in enumerate(serp_data.get("organic_results", [])[:5]):
                    raw_sources.append({
                        "index": i+1, "title": r.get("title",""),
                        "snippet": r.get("snippet",""), "link": r.get("link","")
                    })
                    search_results_text += f"[{i+1}] {r.get('title','')}\n{r.get('snippet','')}\n\n"
            except Exception as e:
                search_results_text = f"(SerpApi 오류: {e})\n"
                raw_sources = [{"index":1,"title":"검색 오류","snippet":str(e),"link":""}]
        else:
            search_results_text = f"[데모] SERP_API_KEY 미설정. 쿼리: {search_query}\n"
            raw_sources = [{"index":1,"title":"SERP_API_KEY 미설정","snippet":"데모 모드","link":""}]

        # 2. 플랫폼별 맞춤 프롬프트로 keywords JSON 배열 반환
        if not OPENAI_API_KEY:
            demo_keywords = [
                {"keyword": "Barrier Care",     "mentions": 0, "summary": "OPENAI_API_KEY를 설정하면 실제 분석이 실행됩니다."},
                {"keyword": "Glass Skin",        "mentions": 0, "summary": "SERPAPI_KEY 설정 시 실시간 데이터 수집됩니다."},
            ]
            return jsonify({
                "success": True, "keywords": demo_keywords,
                "platform": platform,
                "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M KST"),
            })

        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        # PLATFORM_PROMPTS 딕셔너리에서 플랫폼 맞춤 프롬프트 선택
        if platform == 'Instagram':
            prompt = PLATFORM_PROMPTS['Instagram'].format(
                tags_str=search_results_text,
                sample_txt=f"검색 쿼리: {search_query}",
                limit=limit,
            )
        elif platform == 'YouTube':
            prompt = PLATFORM_PROMPTS['YouTube'].format(
                tags_str=search_results_text,
                sample_txt=f"검색 쿼리: {search_query}",
                limit=limit,
            )
        else:
            # 혹시 다른 플랫폼이 이 라우트로 들어오면 범용 프롬프트
            prompt = f"""너는 K-뷰티 트렌드 수석 애널리스트야.
다음은 {platform} K-뷰티 트렌드 관련 실시간 검색 데이터야.

[검색 데이터]
{search_results_text}

[수행 작업]
현재 {platform}에서 핫한 K-뷰티 트렌드 키워드 TOP {limit}을 도출해.
- 오직 제공된 데이터에서만 추출 (할루시네이션 금지)
- 범용 단어(skin, face, good 등) 배제

[출력 형식 - 마크다운 없이 순수 JSON 배열만]
[
  {{
    "keyword": "영어 키워드",
    "mentions": 숫자,
    "summary": "한국어 2~3줄 분석"
  }}
]"""

        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1500,
        )

        raw = res.choices[0].message.content.strip().replace("```json","").replace("```","").strip()
        keywords = json.loads(raw)

        return jsonify({
            "success":        True,
            "keywords":       keywords,
            "platform":       platform,
            "search_results": raw_sources,
            "analyzed_at":    datetime.now().strftime("%Y-%m-%d %H:%M KST"),
        })

    except Exception as e:
        return jsonify({"error": f"서버 오류: {e}"}), 500


# ─── 국가별 진입 전략 분석 (/api/market-entry) ─────────────────────────────
@app.route("/api/market-entry", methods=["POST"])
def api_market_entry():
    """
    4개 카테고리(진입 장벽, 수출 규제, 문화, 소비자 트렌드) 모두
    SerpApi 실시간 검색 → OpenAI GPT-4 분석으로 통일된 파이프라인.
    """
    try:
        body = request.get_json(force=True)
        target_country      = body.get("target_country", "").strip()
        target_item         = "K-beauty skincare cosmetics"
        selected_categories = body.get("selected_categories",
                                       ["진입 장벽", "수출 규제", "문화", "소비자 트렌드"])

        if not target_country:
            return jsonify({"error": "진출 국가를 입력하세요."}), 400
        if not selected_categories:
            return jsonify({"error": "분석 항목을 하나 이상 선택하세요."}), 400

        # API 키 로드
        SERPAPI_KEY    = os.environ.get("SERPAPI_KEY", "")
        OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

        # ── SerpApi: 선택된 모든 카테고리 검색 ──────────────────────────────
        search_text, search_sources = _fetch_serp_data_for_categories(
            target_country, target_item, selected_categories, SERPAPI_KEY
        )

        # ── Prompt 구성 ──────────────────────────────────────────────────────
        categories_str    = ", ".join(selected_categories)
        excluded          = ", ".join(c for c in _CATEGORY_PROMPT if c not in selected_categories) or "없음"
        analysis_sections = "\n\n".join(
            _CATEGORY_PROMPT[c] for c in selected_categories if c in _CATEGORY_PROMPT
        )

        system_prompt = (
            "당신은 10년 경력의 글로벌 뷰티 전략 컨설턴트입니다.\n"
            f"[분석 요청 항목]: {categories_str}\n"
            f"[분석 제외 항목]: {excluded} — 이 항목은 절대 언급하지 마세요.\n\n"
            "데이터 처리 원칙:\n"
            "  - 아래 제공된 실시간 검색 데이터만을 근거로 분석하세요.\n"
            "  - 검색 데이터에 없는 내용은 반드시 '정보 없음' 또는 '추가 확인 필요'로 표기하세요.\n"
            "  - 없는 사실을 절대 지어내지 마세요 (할루시네이션 금지).\n"
            "  - 답변은 마크다운 형식(## 헤딩, - 리스트, **볼드**)으로 작성하세요.\n"
            "  - 검색 데이터를 근거로 서술할 때는 반드시 해당 출처 번호를 [1], [2] 형태로 문장 끝에 표기하세요.\n"
            "  - 각주 번호는 아래 검색 데이터의 [SOURCE 번호]와 일치해야 합니다."
        )

        # 번호 붙인 출처 목록 생성
        numbered_sources = ""
        for i, s in enumerate(search_sources, 1):
            numbered_sources += f"[SOURCE {i}] ({s.get('category','')}) {s.get('title','')}\n{s.get('snippet','')}\n\n"

        item_line   = f"**분석 품목:** {target_item}\n" if target_item else ""
        user_prompt = (
            f"**분석 대상국:** {target_country}\n"
            f"{item_line}"
            f"**분석 요청 항목:** {categories_str}\n\n"
            f"**실시간 검색 데이터 (SerpApi Google Search):**\n"
            f"{numbered_sources}\n"
            f"위 데이터를 근거로 선택된 항목만 분석하세요. 근거 문장마다 [SOURCE 번호]를 [1], [2] 형태로 반드시 표기하세요:\n\n"
            f"{analysis_sections}\n"
        )

        # ── OpenAI GPT-4 분석 (전략 패턴: _call_openai 위임) ──────────────────
        _demo = (
            f"## ⚠️ 데모 모드 (OPENAI_API_KEY 미설정)\n\n"
            f"**분석 대상:** {target_country}"
            + (f" × {target_item}" if target_item else "") + "\n"
            f"**선택 항목:** {categories_str}\n\n"
            + "\n\n".join(
                f"### {c}\n- .env에 OPENAI_API_KEY를 설정하면 실제 분석이 실행됩니다."
                for c in selected_categories
            )
        )
        try:
            analysis_text = _call_openai(
                OPENAI_API_KEY, "gpt-4o",
                [{"role": "system", "content": system_prompt},
                 {"role": "user",   "content": user_prompt}],
                temperature=0.3, max_tokens=1800,
            )
        except ValueError:
            analysis_text = _demo
        except Exception as e:
            analysis_text = f"> ⚠️ OpenAI 오류: {e}\n\n.env에 OPENAI_API_KEY를 설정하세요."

        # ── 응답 반환 ────────────────────────────────────────────────────────
        return jsonify({
            "success":        True,
            "analysis":       analysis_text,
            "system_prompt":  system_prompt,
            "search_results": search_sources,
            "analyzed_at":    datetime.now().strftime("%Y-%m-%d %H:%M KST"),
            "debug": {
                "search_sources":   search_sources,
                "search_text":      search_text,
                "system_prompt":    system_prompt,
                "selected_categories": selected_categories,
                "timestamp":        datetime.now().strftime("%Y-%m-%d %H:%M:%S KST"),
            }
        })

    except Exception as e:
        return jsonify({"error": f"서버 오류: {e}"}), 500

# ─── 콘텐츠 생성 엔진 (Instagram / 숏폼 등) — 비동기 Task 구조 ───────────
@app.route("/api/generate-content", methods=["POST"])
def api_generate_content():
    data = request.get_json(force=True)
    content_type    = data.get("type", "instagram")
    product_name    = data.get("product_name", "").strip()
    target_audience = data.get("target_audience", "").strip()
    key_point       = data.get("key_point", "").strip()
    custom_prompt   = data.get("system_prompt", "").strip()

    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    if not OPENAI_API_KEY:
        return jsonify({"success": False, "error": ".env 파일에 OPENAI_API_KEY가 없습니다."})

    task_id = str(uuid.uuid4())
    _tasks[task_id] = {"status": "processing"}

    def _do_generate():
        try:
            if custom_prompt:
                system_prompt = custom_prompt
            else:
                system_prompt = "당신은 트렌디하고 감각적인 K-뷰티 전문 SNS 콘텐츠 마케터입니다. (이하 생략)"

            user_prompt = f"📦 제품명: {product_name}\n"
            if target_audience: user_prompt += f"🎯 타겟 고객: {target_audience}\n"
            if key_point:        user_prompt += f"✨ 소구 포인트: {key_point}\n"

            result_text = _call_openai(
                OPENAI_API_KEY, "gpt-4-turbo-preview",
                [{"role": "system", "content": system_prompt},
                 {"role": "user",   "content": user_prompt}],
                temperature=0.7, max_tokens=1000,
            )
            _tasks[task_id] = {"status": "done", "result": result_text}
        except Exception as e:
            _tasks[task_id] = {"status": "error", "error": str(e)}

    threading.Thread(target=_do_generate, daemon=True).start()
    return jsonify({"task_id": task_id, "status": "processing"}), 202


@app.route("/api/task-status/<task_id>", methods=["GET"])
def api_task_status(task_id):
    task = _tasks.get(task_id)
    if not task:
        return jsonify({"status": "not_found"}), 404
    return jsonify(task)


# ─────────────────────── Reddit 트렌드 분석 (Apify + OpenAI) ──────────────
@app.route('/api/trend/reddit', methods=['POST'])
def api_trend_reddit():
    """
    Apify로 Reddit K-뷰티 게시글 수집 → OpenAI gpt-4o-mini로 트렌드 키워드 분석
    subreddit: 'AsianBeauty' 또는 'SkincareAddiction' (파라미터로 선택 가능)
    """
    try:
        APIFY_TOKEN    = os.environ.get('APIFY_API_TOKEN', '')
        OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

        if not APIFY_TOKEN:
            return jsonify({'error': '.env에 APIFY_API_TOKEN이 설정되어 있지 않습니다.'}), 400
        if not OPENAI_API_KEY:
            return jsonify({'error': '.env에 OPENAI_API_KEY가 설정되어 있지 않습니다.'}), 400

        # 프론트에서 어떤 서브레딧인지 받기
        body_data  = request.get_json(force=True) or {}
        subreddit  = body_data.get('subreddit', 'AsianBeauty')
        limit      = 15 if body_data.get('platform', '') == '전체' else 10

        # 허용된 서브레딧만 사용 (보안)
        ALLOWED = {
            'AsianBeauty':       'https://www.reddit.com/r/AsianBeauty/new/',
            'SkincareAddiction': 'https://www.reddit.com/r/SkincareAddiction/new/',
        }
        reddit_url = ALLOWED.get(subreddit, ALLOWED['AsianBeauty'])

        # ── 1. Apify로 Reddit 게시글 수집 ──────────────────────────────────
        from apify_client import ApifyClient
        client = ApifyClient(APIFY_TOKEN)

        run_input = {
            'startUrls': [{'url': reddit_url}],
            'maxItems': 50,
            'proxyConfiguration': {'useApifyProxy': True},
        }

        def _scrape_reddit():
            r = client.actor('trudax/reddit-scraper-lite').call(run_input=run_input)
            return list(client.dataset(r['defaultDatasetId']).iterate_items())

        items = _run_with_timeout(_scrape_reddit, timeout=120)

        if not items:
            return jsonify({'error': 'Apify에서 데이터를 가져오지 못했습니다.'}), 500

        # ── 2. 텍스트 합치기 ────────────────────────────────────────────────
        combined_text = ''
        for item in items:
            title = item.get('title') or item.get('title_text') or ''
            body  = item.get('text')  or item.get('selftext') or item.get('body') or ''
            if title or body:
                combined_text += f'제목: {title}\n본문: {body}\n\n'

        # ── 3. OpenAI gpt-4o-mini 분석 (전략 패턴: _call_openai 위임) ────────
        prompt = PLATFORM_PROMPTS['Reddit'].format(
            combined_text=combined_text,
            limit=limit,
        )

        raw = _call_openai(
            OPENAI_API_KEY, 'gpt-4o-mini',
            [{'role': 'user', 'content': prompt}],
            temperature=0.3, max_tokens=1000,
        )
        raw      = raw.strip().replace('```json', '').replace('```', '').strip()
        keywords = json.loads(raw)

        # mentions 내림차순 정렬 (백엔드에서 확실히 보장)
        keywords = sorted(keywords, key=lambda x: x.get('mentions', 0), reverse=True)

        return jsonify({
            'success':    True,
            'keywords':   keywords,
            'post_count': len(items),
            'subreddit':  subreddit,
            'analyzed_at': datetime.now().strftime('%Y-%m-%d %H:%M KST'),
        })

    except Exception as e:
        return jsonify({'error': f'분석 오류: {str(e)}'}), 500


# ─────────────────────── YouTube 트렌드 분석 (Apify + OpenAI) ─────────────
@app.route('/api/trend/youtube', methods=['POST'])
def api_trend_youtube():
    """
    Apify youtube-scraper로 K-뷰티 관련 유튜브 영상 수집 (미국 타겟, 총 30개)
    제목+설명 통합 분석 → gpt-4o-mini로 트렌드 키워드 TOP10 추출 (언급량 내림차순)
    """
    try:
        APIFY_TOKEN    = os.environ.get('APIFY_API_TOKEN', '')
        OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

        if not APIFY_TOKEN:
            return jsonify({'error': '.env에 APIFY_API_TOKEN이 설정되어 있지 않습니다.'}), 400
        if not OPENAI_API_KEY:
            return jsonify({'error': '.env에 OPENAI_API_KEY가 설정되어 있지 않습니다.'}), 400

        # ── 1. Apify로 유튜브 영상 수집 ──────────────────────────────────
        from apify_client import ApifyClient
        client = ApifyClient(APIFY_TOKEN)

        keyword_list = [
            "Best skincare routine 2026",
            "Morning skincare for glowing skin",
            "Skincare steps for healthy skin",
        ]

        all_items = []
        for keyword in keyword_list:
            try:
                _ri = {
                    'searchQueries': [keyword],
                    'maxResults': 3,
                    'proxyConfiguration': {
                        'useApifyProxy': True,
                        'apifyProxyCountry': 'US',
                    },
                }
                def _scrape_yt(ri=_ri):
                    r = client.actor('streamers/youtube-scraper').call(run_input=ri)
                    return list(client.dataset(r['defaultDatasetId']).iterate_items())
                items = _run_with_timeout(_scrape_yt, timeout=90)
                all_items.extend(items)
            except Exception as e:
                print(f"[YouTube] '{keyword}' 수집 오류: {e}")
                continue

        if not all_items:
            return jsonify({'error': 'Apify에서 유튜브 데이터를 가져오지 못했습니다.'}), 500

        # ── 2. 제목 + 설명 합치기 ─────────────────────────────────────────
        combined_text = ''
        for item in all_items:
            title       = item.get('title') or item.get('name') or ''
            description = item.get('description') or item.get('text') or ''
            if title or description:
                combined_text += f'제목: {title}\n설명: {description}\n\n'

        # ── 3. OpenAI gpt-4o-mini 분석 (전략 패턴: _call_openai 위임) ────────
        prompt = PLATFORM_PROMPTS['YouTube'].format(
            tags_str=combined_text,
            sample_txt='',
            limit=10,
        )

        raw = _call_openai(
            OPENAI_API_KEY, 'gpt-4o-mini',
            [{'role': 'user', 'content': prompt}],
            temperature=0.3, max_tokens=1500,
        )
        raw      = raw.strip().replace('```json', '').replace('```', '').strip()
        keywords = json.loads(raw)

        # mentions 내림차순 정렬 (백엔드에서 확실히 보장)
        keywords = sorted(keywords, key=lambda x: x.get('mentions', 0), reverse=True)

        return jsonify({
            'success':    True,
            'keywords':   keywords,
            'video_count': len(all_items),
            'analyzed_at': datetime.now().strftime('%Y-%m-%d %H:%M KST'),
        })

    except Exception as e:
        return jsonify({'error': f'분석 오류: {str(e)}'}), 500


# ─────────────────────── TikTok 트렌드 분석 (Apify + OpenAI) ──────────────
@app.route('/api/trend/tiktok', methods=['POST'])
def api_trend_tiktok():
    """
    Apify clockworks/tiktok-scraper로 미국 뷰티 영상 수집
    → EXCLUDED_TAGS 필터링 후 Python Counter로 해시태그 중복 빈도 순위 추출
    → PLATFORM_PROMPTS['TikTok'] - Z세대 숏폼 바이럴 마케터 관점으로 gpt-4o-mini 분석

    platform 값에 따라 반환 개수 결정:
      - "전체" → TOP 15
      - 그 외 개별 플랫폼 → TOP 10
    """
    try:
        APIFY_TOKEN    = os.environ.get('APIFY_API_TOKEN', '')
        OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

        if not APIFY_TOKEN:
            return jsonify({'error': '.env에 APIFY_API_TOKEN이 설정되어 있지 않습니다.'}), 400
        if not OPENAI_API_KEY:
            return jsonify({'error': '.env에 OPENAI_API_KEY가 설정되어 있지 않습니다.'}), 400

        body_data = request.get_json(force=True) or {}
        platform  = body_data.get('platform', 'TikTok')
        limit = 15 if platform == '전체' else 10

        from apify_client import ApifyClient
        from collections import Counter

        client = ApifyClient(APIFY_TOKEN)

        run_input = {
            "hashtags": ["sephorahaul", "skincare", "beauty"],
            "resultsPerPage": 1,
            "proxyConfiguration": {"useApifyProxy": True},
        }

        def _scrape_tt():
            r = client.actor('clockworks/tiktok-scraper').call(run_input=run_input)
            return list(client.dataset(r['defaultDatasetId']).iterate_items())

        items = _run_with_timeout(_scrape_tt, timeout=90)

        if not items:
            return jsonify({'error': 'Apify에서 TikTok 데이터를 가져오지 못했습니다.'}), 500

        counter     = Counter()
        video_texts = []

        for item in items:
            raw_tags = item.get('hashtags') or []
            for tag in raw_tags:
                if isinstance(tag, dict):
                    name = tag.get('name', '')
                elif isinstance(tag, str):
                    name = tag.replace('#', '')
                else:
                    continue
                name = name.lower().strip()
                if name and name not in EXCLUDED_TAGS:
                    counter[name] += 1
            text = item.get('text', '')
            if text:
                video_texts.append(text[:200])

        if not counter:
            return jsonify({'error': '수집된 영상에서 해시태그를 찾을 수 없습니다.'}), 500

        top_tags = counter.most_common(limit)

        tags_str   = ', '.join([f'{tag}({cnt}회)' for tag, cnt in top_tags])
        sample_txt = '\n'.join(video_texts[:20])

        prompt = PLATFORM_PROMPTS['TikTok'].format(
            tags_str=tags_str,
            sample_txt=sample_txt,
            limit=limit,
        )

        # OpenAI 분석 (전략 패턴: _call_openai 위임)
        raw = _call_openai(
            OPENAI_API_KEY, 'gpt-4o-mini',
            [{'role': 'user', 'content': prompt}],
            temperature=0.3, max_tokens=1500,
        )
        raw      = raw.strip().replace('```json', '').replace('```', '').strip()
        keywords = json.loads(raw)
        keywords = sorted(keywords, key=lambda x: x.get('mentions', 0), reverse=True)

        return jsonify({
            'success':     True,
            'keywords':    keywords,
            'video_count': len(items),
            'platform':    'TikTok',
            'analyzed_at': datetime.now().strftime('%Y-%m-%d %H:%M KST'),
        })

    except Exception as e:
        return jsonify({'error': f'TikTok 분석 오류: {str(e)}'}), 500


# ─────────────────────── Instagram 트렌드 분석 (Apify + OpenAI) ────────────
@app.route('/api/trend/instagram', methods=['POST'])
def api_trend_instagram():
    """
    Apify apify/instagram-hashtag-scraper로 미국 K-뷰티 게시물 수집
    → EXCLUDED_TAGS 필터링 후 Python Counter로 해시태그 중복 빈도 순위 추출
    → PLATFORM_PROMPTS['Instagram'] - 비주얼·인플루언서 마케터 관점으로 gpt-4o-mini 분석
    """
    try:
        APIFY_TOKEN    = os.environ.get('APIFY_API_TOKEN', '')
        OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

        if not APIFY_TOKEN:
            return jsonify({'error': '.env에 APIFY_API_TOKEN이 설정되어 있지 않습니다.'}), 400
        if not OPENAI_API_KEY:
            return jsonify({'error': '.env에 OPENAI_API_KEY가 설정되어 있지 않습니다.'}), 400

        from apify_client import ApifyClient
        from collections import Counter

        client = ApifyClient(APIFY_TOKEN)

        run_input = {
            'hashtags': ['skincareroutine', 'skincaretips', 'healthyskin'],
            'resultsLimit': 20,
            'proxyConfiguration': {'useApifyProxy': True},
        }

        def _scrape_ig():
            r = client.actor('apify/instagram-hashtag-scraper').call(run_input=run_input)
            return list(client.dataset(r['defaultDatasetId']).iterate_items())

        items = _run_with_timeout(_scrape_ig, timeout=90)

        if not items:
            return jsonify({'error': 'Apify에서 Instagram 데이터를 가져오지 못했습니다.'}), 500

        counter     = Counter()
        post_texts  = []

        for item in items:
            raw_tags = item.get('hashtags') or item.get('taggedUsers') or []
            caption  = item.get('caption') or item.get('text') or ''

            # 캡션에서 해시태그 직접 파싱
            import re
            tags_in_caption = re.findall(r'#(\w+)', caption.lower())
            for name in tags_in_caption:
                name = name.strip()
                if name and name not in EXCLUDED_TAGS:
                    counter[name] += 1

            for tag in raw_tags:
                if isinstance(tag, dict):
                    name = tag.get('name', '') or tag.get('id', '')
                elif isinstance(tag, str):
                    name = tag.replace('#', '')
                else:
                    continue
                name = name.lower().strip()
                if name and name not in EXCLUDED_TAGS:
                    counter[name] += 1

            if caption:
                post_texts.append(caption[:200])

        if not counter:
            return jsonify({'error': '수집된 게시물에서 해시태그를 찾을 수 없습니다.'}), 500

        limit    = 10
        top_tags = counter.most_common(limit)

        tags_str   = ', '.join([f'{tag}({cnt}회)' for tag, cnt in top_tags])
        sample_txt = '\n'.join(post_texts[:20])

        prompt = PLATFORM_PROMPTS['Instagram'].format(
            tags_str=tags_str,
            sample_txt=sample_txt,
            limit=limit,
        )

        # OpenAI 분석 (전략 패턴: _call_openai 위임)
        raw = _call_openai(
            OPENAI_API_KEY, 'gpt-4o-mini',
            [{'role': 'user', 'content': prompt}],
            temperature=0.3, max_tokens=1500,
        )
        raw      = raw.strip().replace('```json', '').replace('```', '').strip()
        keywords = json.loads(raw)
        keywords = sorted(keywords, key=lambda x: x.get('mentions', 0), reverse=True)

        return jsonify({
            'success':     True,
            'keywords':    keywords,
            'post_count':  len(items),
            'platform':    'Instagram',
            'analyzed_at': datetime.now().strftime('%Y-%m-%d %H:%M KST'),
        })

    except Exception as e:
        return jsonify({'error': f'Instagram 분석 오류: {str(e)}'}), 500


# ─────────────────────── 전체 플랫폼 통합 트렌드 (TikTok + Reddit) ─────────
@app.route('/api/trend/all', methods=['POST'])
def api_trend_all():
    """
    TikTok(Apify) + Reddit(Apify) 둘 다 수집 후 OpenAI로 통합 TOP 15 분석
    ✅ EXCLUDED_TAGS 필터링 적용 - 미끼 태그 제외한 순수 트렌드만 추출
    """
    try:
        APIFY_TOKEN    = os.environ.get('APIFY_API_TOKEN', '')
        OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

        if not APIFY_TOKEN or not OPENAI_API_KEY:
            return jsonify({'error': '.env에 APIFY_API_TOKEN과 OPENAI_API_KEY를 설정하세요.'}), 400

        from apify_client import ApifyClient
        from collections import Counter
        from openai import OpenAI

        client      = ApifyClient(APIFY_TOKEN)
        tt_counter  = Counter()
        all_text    = ''
        tt_count    = 0
        rd_count    = 0

        # ── 1. TikTok 수집 + EXCLUDED_TAGS 필터링 ──────────────────────────
        try:
            _tt_input = {
                "hashtags": ["sephorahaul", "skincare", "beauty"],
                "resultsPerPage": 3,
                "proxyConfiguration": {"useApifyProxy": True},
            }
            def _scrape_tt_all():
                r = client.actor('clockworks/tiktok-scraper').call(run_input=_tt_input)
                return list(client.dataset(r['defaultDatasetId']).iterate_items())
            tt_items = _run_with_timeout(_scrape_tt_all, timeout=90)
            tt_count = len(tt_items)

            for item in tt_items:
                for tag in (item.get('hashtags') or []):
                    name = (tag.get('name','') if isinstance(tag, dict) else tag).lower().strip().replace('#','')
                    if name and name not in EXCLUDED_TAGS:
                        tt_counter[name] += 1
                if item.get('text'):
                    all_text += f"[TikTok캡션] {item['text'][:150]}\n"

            top_tt = tt_counter.most_common(20)
            if top_tt:
                tt_tags_str = ', '.join([f'#{t}({c}회)' for t, c in top_tt])
                all_text = f"[TikTok 핫 해시태그] {tt_tags_str}\n" + all_text

        except Exception as e:
            all_text += f"[TikTok 수집 오류: {e}]\n"

        # ── 2. Reddit 수집 ─────────────────────────────────────────────────
        try:
            _rd_input = {
                'startUrls': [{'url': 'https://www.reddit.com/r/AsianBeauty/new/'}],
                'maxItems': 30,
                'proxyConfiguration': {'useApifyProxy': True},
            }
            def _scrape_rd_all():
                r = client.actor('trudax/reddit-scraper-lite').call(run_input=_rd_input)
                return list(client.dataset(r['defaultDatasetId']).iterate_items())
            rd_items = _run_with_timeout(_scrape_rd_all, timeout=120)
            rd_count = len(rd_items)

            for item in rd_items:
                title = item.get('title','')
                body  = item.get('text','') or item.get('selftext','') or ''
                if title or body:
                    all_text += f"[Reddit] {title} {body[:120]}\n"
        except Exception as e:
            all_text += f"[Reddit 수집 오류: {e}]\n"

        if not all_text.strip():
            return jsonify({'error': '수집된 데이터가 없습니다.'}), 500

        # ── 3. OpenAI 통합 분석 TOP 15 (전략 패턴: _call_openai 위임) ─────────
        prompt = f"""너는 K-뷰티 및 글로벌 스킨케어 시장 트렌드를 분석하는 수석 데이터 애널리스트이자 마케팅 전략가야.
다음은 TikTok과 Reddit에서 실시간으로 수집된 미국 K-뷰티 관련 콘텐츠야.
TikTok 데이터는 EXCLUDED_TAGS(sephorahaul, beauty, skincare, fyp 등 범용 태그)가 이미 필터링된 순수 트렌드 데이터야.

[수집 데이터]
{all_text[:4000]}

[수행 작업 및 분석 가이드라인]
TikTok 해시태그 빈도 + Reddit 게시글 내용을 합산하여, 현재 미국에서 가장 핫한 K-뷰티 트렌드 키워드 **TOP 15**를 도출해.
단, 다음의 깐깐한 규칙을 무조건 엄수해야 해.

1. 절대적 객관성 유지 (할루시네이션 금지):
   - 오직 제공된 데이터에서만 추출할 것. 데이터에 없는 내용을 지어내지 말 것.

2. 미끼 태그 완전 배제:
   - K-Beauty, skincare, beauty, kbeauty, sephorahaul 같은 범용/미끼 태그는 절대 키워드로 선정하지 말 것.
   - 특정 성분명, 피부 고민, 제형, 제품 카테고리 등 마케팅에 즉시 활용 가능한 구체적 키워드만 선정할 것.

3. 키워드 그룹핑:
   - 비슷한 의미(예: Sunscreen/SPF/Sunblock)는 가장 대표적인 하나로 통합할 것.

4. 플랫폼 출처 표기:
   - TikTok 해시태그에서만 나온 것은 "tiktok", Reddit 게시글에서만 나온 것은 "reddit", 둘 다면 "both"로 표기.

[출력 형식 - 마크다운 기호 없이 순수한 JSON 배열만 출력]
[
  {{
    "keyword": "영어 키워드 (구체적 성분/고민/카테고리)",
    "mentions": 숫자,
    "source": "tiktok 또는 reddit 또는 both",
    "summary": "이 키워드가 현재 미국 K-뷰티 시장에서 왜 핫한지, TikTok과 Reddit의 유저 반응을 종합하여 마케팅 관점 2~3줄 한국어 분석"
  }}
]"""

        raw = _call_openai(
            OPENAI_API_KEY, 'gpt-4o-mini',
            [{'role': 'user', 'content': prompt}],
            temperature=0.3, max_tokens=2500,
        )
        raw      = raw.strip().replace('```json','').replace('```','').strip()
        keywords = json.loads(raw)
        keywords = sorted(keywords, key=lambda x: int(x.get('mentions', 0) if x.get('mentions') else 0), reverse=True)

        return jsonify({
            'success':     True,
            'keywords':    keywords[:15],
            'platform':    '전체',
            'tt_count':    tt_count,
            'rd_count':    rd_count,
            'analyzed_at': datetime.now().strftime('%Y-%m-%d %H:%M KST'),
        })

    except Exception as e:
        return jsonify({'error': f'전체 분석 오류: {str(e)}'}), 500


# ═══════════════════════════════════════════════════════════════════════════
# 아웃리치 API — 크리에이터 / 전시회 / 유통채널 / 이메일 발송
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/api/outreach/creators', methods=['GET','POST'])
def api_outreach_creators():
    """
    미국 뷰티 마이크로 크리에이터 수집 v5 ─ 완전 재설계
    ─────────────────────────────────────────────────────
    ★ 팔로워 우선순위: 마이크로 30K~200K (1순위) / 차선 210K~300K (2순위)
    ★ 중복 방지: POST body excluded_handles + call_index → 매 호출마다 다른 크리에이터
    ★ 쿼리 전략:
       Pass 1 큐레이션DB (thesocialcat·heepsy·modash) — 팔로워 수치 스니펫 직접 노출
       Pass 2 TikTok 프로필 직접 (@핸들 URL 패턴)
       Pass 3 Instagram 프로필
       Pass 4 YouTube 소형 채널
       Pass 5 Reddit 추천 스레드
    ★ 이메일: 스니펫 "@" 확인된 것만 / 불확실 → "[ SNS에서 직접 확인 ]"
    ★ 나이: 스니펫 명확 수치만 정수 / 불확실 → null / 범위 표시 금지
    """
    try:
        SERPAPI_KEY    = os.environ.get('SERPAPI_KEY', '')
        OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
        if not SERPAPI_KEY or not OPENAI_API_KEY:
            return jsonify({'error': '.env에 SERPAPI_KEY와 OPENAI_API_KEY를 설정하세요.'}), 400

        import urllib.request, urllib.parse, time, re, random as _rand
        from openai import OpenAI
        ai = OpenAI(api_key=OPENAI_API_KEY)

        # ── 요청 파라미터 파싱 ──────────────────────────────────────────────
        req = request.get_json(silent=True) or {}
        excluded_raw  = req.get('excluded_handles', [])
        excluded_set  = set(h.lstrip('@').lower().strip() for h in excluded_raw if h)
        call_idx      = int(req.get('call_index', 0))
        print(f'\n[Creator v5] 회차={call_idx+1}, 제외={len(excluded_set)}개')

        # ── SerpApi 유틸 ────────────────────────────────────────────────────
        def serp(q: str, n: int = 10) -> str:
            params = urllib.parse.urlencode({
                'q': q, 'api_key': SERPAPI_KEY, 'num': n, 'hl': 'en', 'gl': 'us'
            })
            try:
                with urllib.request.urlopen(
                    f'https://serpapi.com/search.json?{params}', timeout=20
                ) as r:
                    d = json.loads(r.read().decode())
                rows = d.get('organic_results', [])[:n]
                return '\n'.join(
                    f"[{i+1}] {row.get('title','')} | {row.get('link','')}\n    {row.get('snippet','')}"
                    for i, row in enumerate(rows)
                )
            except Exception as e:
                return f'(검색오류: {e})'

        # ── 이메일 추출 유틸 ────────────────────────────────────────────────
        def get_emails(text: str) -> list:
            found = re.findall(r'[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}', text)
            bad   = {'serpapi.com','google.com','example.com','sentry.io','cloudflare.com'}
            return list({e for e in found if not any(b in e for b in bad)})

        # ══════════════════════════════════════════════════════════════════════
        # 쿼리 풀 — call_idx 기반 오프셋으로 매 호출마다 완전히 다른 조합
        # ══════════════════════════════════════════════════════════════════════

        CUR_POOL = [
            'site:thesocialcat.com beauty skincare "United States" followers micro influencer',
            'site:thesocialcat.com "skincare" "k-beauty" "United States" followers email micro',
            'site:thesocialcat.com "beauty" "sunscreen" OR "SPF" "United States" micro influencer followers',
            'site:thesocialcat.com makeup skincare "United States" micro influencer followers email',
            'site:thesocialcat.com beauty influencer "United States" "30K" OR "50K" OR "100K" followers',
            'site:thesocialcat.com "korean beauty" OR "kbeauty" "United States" influencer followers',
            'site:heepsy.com beauty skincare micro influencer "United States" followers TikTok Instagram',
            'site:heepsy.com "k-beauty" "korean skincare" US micro influencer followers engagement',
            'site:heepsy.com beauty influencer United States 30K 200K TikTok Instagram followers',
            'site:modash.io beauty skincare "United States" micro influencer followers TikTok',
            'site:modash.io US beauty influencer skincare followers email platform TikTok Instagram',
            'site:collabstr.com beauty skincare "United States" TikTok micro influencer followers',
            'site:collabstr.com "k-beauty" OR "korean skincare" US influencer followers rate',
            '"beauty micro influencer" "United States" TikTok followers 30K 100K skincare collab 2025',
            '"skincare micro influencer" United States Instagram TikTok followers 50K 200K email 2025',
            '"beauty influencer" United States TikTok 30K OR 50K OR 80K OR 100K followers skincare',
        ]

        TT_POOL = [
            'site:tiktok.com/@ "skincare" "United States" followers beauty micro influencer',
            'site:tiktok.com/@ "k-beauty" OR "korean skincare" United States followers micro',
            'site:tiktok.com/@ "SPF" OR "sunscreen" skincare United States beauty creator followers',
            'site:tiktok.com/@ "glass skin" OR "skin barrier" United States beauty micro creator',
            'site:tiktok.com/@ "serum" OR "moisturizer" skincare routine United States beauty followers',
            'site:tiktok.com/@ "honest review" skincare beauty United States followers micro',
            'site:tiktok.com/@ "acne" OR "redness" skincare United States beauty creator followers',
            'site:tiktok.com/@ "snail mucin" OR "niacinamide" beauty United States micro creator',
            'site:tiktok.com/@ beauty skincare "Los Angeles" OR "New York" OR "Chicago" followers',
            'site:tiktok.com/@ beauty skincare "Texas" OR "Florida" OR "Georgia" US followers',
            'site:tiktok.com/@ "clean beauty" OR "fragrance free" United States skincare followers',
            'site:tiktok.com/@ beauty skincare influencer United States email collab PR followers',
            'site:tiktok.com/@ makeup skincare routine United States female beauty micro creator',
            'site:tiktok.com/@ "spf" "sunscreen" "skin" United States beauty content creator',
            'site:tiktok.com/@ "dermatologist" OR "esthetician" United States skincare creator',
            'site:tiktok.com/@ "product review" skincare beauty United States micro influencer',
        ]

        IG_POOL = [
            'site:instagram.com "skincare" "k-beauty" "email" OR "collab" United States followers micro',
            'site:instagram.com "sunscreen" OR "SPF" skincare United States beauty micro influencer',
            'site:instagram.com "korean skincare" United States micro beauty creator email followers',
            'site:instagram.com "glass skin" "collab" OR "PR" United States skincare creator',
            'site:instagram.com "skin barrier" OR "snail mucin" United States beauty micro influencer',
            'site:instagram.com "clean beauty" "collab" United States female skincare creator',
            '"instagram beauty micro influencer" United States skincare 30K 100K 200K followers 2025',
            '"instagram skincare influencer" United States 30K 50K 100K followers email collab 2025',
        ]

        YT_POOL = [
            'site:youtube.com "skincare" "United States" subscribers micro beauty channel',
            'site:youtube.com "k-beauty" "korean skincare routine" United States small channel',
            'site:youtube.com "honest skincare review" "SPF" United States beauty micro creator',
            '"youtube beauty channel" United States skincare micro creator 30K 100K subscribers 2025',
            'site:youtube.com "sunscreen review" OR "spf review" United States beauty channel micro',
        ]

        RD_POOL = [
            'site:reddit.com/r/SkincareAddiction "creator" OR "influencer" recommend United States TikTok',
            'site:reddit.com "underrated beauty creator" United States skincare TikTok Instagram 2025',
            'site:reddit.com "small skincare creator" OR "micro influencer" US recommend 2024 2025',
            'site:reddit.com r/beauty "favorite influencer" United States skincare TikTok micro',
            'site:reddit.com "beauty tiktok" recommend United States skincare creator under 200K',
        ]

        _rand.seed(int(time.time() * 10000) % 99991 + call_idx * 7919)

        def pick(pool, count, idx_offset):
            start = (call_idx * idx_offset) % len(pool)
            return [pool[(start + i) % len(pool)] for i in range(count)]

        cur_qs = pick(CUR_POOL, 3, 3)
        tt_qs  = pick(TT_POOL,  4, 5)
        ig_qs  = pick(IG_POOL,  2, 7)
        yt_q   = YT_POOL[call_idx % len(YT_POOL)]
        rd_q   = RD_POOL[call_idx % len(RD_POOL)]

        raw = {}

        raw['cur'] = ''
        for q in cur_qs:
            raw['cur'] += f'[Q: {q}]\n' + serp(q, 10) + '\n\n'
            time.sleep(0.4)

        raw['tt'] = ''
        for q in tt_qs:
            raw['tt'] += f'[Q: {q}]\n' + serp(q, 10) + '\n\n'
            time.sleep(0.4)

        raw['ig'] = ''
        for q in ig_qs:
            raw['ig'] += f'[Q: {q}]\n' + serp(q, 8) + '\n\n'
            time.sleep(0.4)

        raw['yt'] = f'[Q: {yt_q}]\n' + serp(yt_q, 7) + '\n\n'
        time.sleep(0.4)

        raw['rd'] = f'[Q: {rd_q}]\n' + serp(rd_q, 7) + '\n\n'

        all_text  = '\n'.join(raw.values())
        emails    = get_emails(all_text)
        email_hint = (
            f"✅ 스니펫 파싱 이메일 {len(emails)}개: {', '.join(emails)}\n→ 문맥 확인 후 사용"
            if emails else "❌ 이메일 미발견 → 전원 '[ SNS에서 직접 확인 ]'"
        )

        ex_hint = (
            f"⛔ 이미 조사됨(절대 재사용 금지): {', '.join('@'+h for h in excluded_set)}"
            if excluded_set else "※ 첫 번째 조사 (제외 핸들 없음)"
        )

        prompt = f"""K-뷰티 브랜드 Klear 인플루언서 마케팅 수석 리서처.
아래는 미국 뷰티 마이크로 크리에이터 조사를 위한 SerpApi 검색 결과 (호출 회차: {call_idx+1}).

[큐레이션 DB — thesocialcat·heepsy·modash (팔로워 수치 직접 노출, ★최우선)]
{raw['cur'][:3500]}

[TikTok 프로필 직접 — 4쿼리]
{raw['tt'][:3500]}

[Instagram 프로필]
{raw['ig'][:2000]}

[YouTube 소형 채널]
{raw['yt'][:1200]}

[Reddit 추천 스레드]
{raw['rd'][:1200]}

[이메일 파싱 결과]
{email_hint}

[중복 방지]
{ex_hint}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[작업 지시] 정확히 10명 도출
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

★ 팔로워 우선순위:
   1순위: 마이크로 30K~200K → 이 범위로 최대한 채울 것
   2순위: 차선 210K~300K  → 1순위 부족 시만
   (300K 초과 대형 인플루언서 포함 금지)

★ TikTok 크리에이터 최소 5명 이상
★ 뷰티/스킨케어/K-뷰티/메이크업 분야만 (육아·음식·여행 절대 금지)
★ 검색 결과 스니펫·URL·제목에 실제 등장한 핸들·이름만 사용

⛔ 절대 금지:
① 스니펫·URL에 없는 인물 창작
② {', '.join('@'+h for h in list(excluded_set)[:30]) if excluded_set else '없음'} — 재사용 절대 금지
③ 팔로워 300K 초과 대형 인플루언서
④ 나이 범위 표시("22-28" 형식) — 명확 정수 또는 null
⑤ 이메일 창작·추측

✅ 팔로워 수 추출 규칙:
- 스니펫·제목에서 "87K followers", "1.2M", "150K" → 그대로 추출
- 큐레이션 DB 스니펫 수치 최우선
- 수치 불명확 → "30K-200K 추정 (마이크로)" 또는 "미확인"
- 단, 300K 초과 확인되면 포함 금지

✅ profile_url (모든 크리에이터 필수):
- TikTok:    https://www.tiktok.com/@핸들
- Instagram: https://www.instagram.com/핸들
- YouTube:   https://www.youtube.com/@핸들

✅ 이메일: 스니펫 문맥 확인된 것만 / 불확실 → "[ SNS에서 직접 확인 ]"
✅ creator_age: 스니펫 명확 수치만 정수. 없으면 null
✅ audience_age: "주요 팬층 20대~30대" 형식

순수 JSON 배열만 출력 (마크다운 코드블록 없이, 정확히 10개):
[
  {{
    "name": "실제 크리에이터명",
    "handle": "@실제핸들",
    "platform": "TikTok/Instagram/YouTube",
    "multi_platform": ["TikTok", "Instagram"],
    "followers": "예: 87K  또는  30K-200K 추정 (마이크로)  또는  미확인",
    "estimated_er": "5.2% 또는 null",
    "email": "이메일 또는 [ SNS에서 직접 확인 ]",
    "profile_url": "https://...",
    "creator_age": null,
    "audience_age": "주요 팬층 20대~30대",
    "gender": "여성/남성",
    "location": "예: Los Angeles, CA",
    "niche": "스킨케어·선케어·K-뷰티 (한국어 2~3개)",
    "content_style": "포맷·톤·빈도 3줄 (한국어)",
    "audience": "팬층 연령·성별·피부고민·구매성향 2줄 (한국어)",
    "collab_fit": "Klear 선세럼 SPF50+ 협업 적합도 1~2줄 (한국어)"
  }}
]"""

        res = ai.chat.completions.create(
            model='gpt-4o',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.7,
            max_tokens=7000,
        )
        import re as _re
        raw_json = res.choices[0].message.content.strip()
        raw_json = _re.sub(r'^```(?:json)?\s*', '', raw_json, flags=_re.MULTILINE)
        raw_json = _re.sub(r'\s*```$',          '', raw_json, flags=_re.MULTILINE).strip()
        if not raw_json:
            return jsonify({"error": "SerpAPI 크레딧이 부족하거나 응답이 비어있습니다. SerpAPI 대시보드에서 크레딧을 확인하세요."}), 500
        try:
            creators = json.loads(raw_json)
        except json.JSONDecodeError as je:
            return jsonify({"error": f"AI 응답 파싱 실패: {je}"}), 500

        # ── 후처리: 제외 핸들 재필터 (GPT 무시 방어) + 300K 초과 제거 ────────
        def parse_followers_k(s):
            if not s: return 0
            s = str(s).upper().replace(',','')
            m = _re.search(r'([\d.]+)\s*M', s)
            if m: return float(m.group(1)) * 1000
            m = _re.search(r'([\d.]+)\s*K', s)
            if m: return float(m.group(1))
            m = _re.search(r'(\d+)', s)
            if m: return int(m.group(1)) / 1000
            return 0

        filtered = []
        for c in creators:
            h = c.get('handle','').lstrip('@').lower().strip()
            if h in excluded_set:
                print(f'  ⚠ 제외 핸들 필터: @{h}')
                continue
            fk = parse_followers_k(c.get('followers',''))
            if fk > 300:
                print(f'  ⚠ 300K 초과 제거: @{h} ({c.get("followers","")})')
                continue
            filtered.append(c)

        creators = filtered[:10]
        print(f'✅ 최종 {len(creators)}명 (제외 {len(excluded_set)}개 필터 후)')

        return jsonify({
            'success':    True,
            'creators':   creators,
            'total':      len(creators),
            'method':     f'SerpApi v5(큐레이션×3·TikTok×4·IG×2·YT·Reddit) × gpt-4o · 회차{call_idx+1}',
            'call_index': call_idx,
            'analyzed_at': datetime.now().strftime('%Y-%m-%d %H:%M KST'),
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'크리에이터 수집 오류: {str(e)}'}), 500


@app.route('/api/outreach/exhibitions', methods=['GET','POST'])
def api_outreach_exhibitions():
    """
    미국 뷰티 전시회 — 검색 시점 기준 미래 개최분만 반환
    ★ 날짜 완전 동적 처리 (하드코딩 없음)
    ★ 서버사이드 3중 필터: 미국좌표 + 날짜 + 연도
    """
    try:
        SERPAPI_KEY    = os.environ.get('SERPAPI_KEY', '')
        OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
        if not SERPAPI_KEY or not OPENAI_API_KEY:
            return jsonify({'error': '.env에 SERPAPI_KEY와 OPENAI_API_KEY를 설정하세요.'}), 400

        import urllib.request, urllib.parse, time, re as _re
        from openai import OpenAI
        from datetime import datetime as _dt
        ai = OpenAI(api_key=OPENAI_API_KEY)

        today      = _dt.now()
        today_iso  = today.strftime('%Y-%m-%d')
        today_kor  = today.strftime('%Y년 %m월 %d일')
        yr         = today.year
        next_yr    = yr + 1
        search_yrs = f'{yr} OR {next_yr}'
        print(f'\n[Expo] 기준일: {today_iso} / 검색연도: {search_yrs}')

        def serp(q, n=8):
            params = urllib.parse.urlencode({
                'q': q, 'api_key': SERPAPI_KEY, 'num': n, 'hl': 'en', 'gl': 'us'
            })
            try:
                with urllib.request.urlopen(
                    f'https://serpapi.com/search.json?{params}', timeout=20
                ) as r:
                    d = json.loads(r.read().decode())
                rows = d.get('organic_results', [])[:n]
                return '\n'.join(
                    f"[{i+1}] {row.get('title','')} | {row.get('link','')}\n    {row.get('snippet','')}"
                    for i, row in enumerate(rows)
                )
            except Exception as e:
                return f'(검색오류: {e})'

        raw_data = ''
        expo_qs = [
            f'Cosmoprof North America {search_yrs} upcoming Las Vegas United States exhibitor contact cosmoprofna.com',
            f'"Indie Beauty Expo" {search_yrs} upcoming United States exhibitor application indiebeautyexpo.com',
            f'"Natural Products Expo West" {search_yrs} upcoming Anaheim California exhibitor contact newhope.com',
            f'"America\'s Beauty Show" {search_yrs} upcoming Chicago Illinois United States exhibitor americasbeautyshow.com',
            f'CEW Beauty Awards {search_yrs} upcoming New York United States cew.org contact',
            f'"Premiere Orlando" {search_yrs} upcoming Florida beauty show exhibitor contact premierebeauty.com',
            f'IECSC {search_yrs} upcoming United States esthetics cosmetology expo contact iecsc.com',
            f'International Beauty Show IBS {search_yrs} New York United States exhibitor contact',
            f'"Beautycon" {search_yrs} Los Angeles United States brand partnership upcoming',
            f'upcoming US beauty trade show expo {search_yrs} exhibitor application United States contact email',
        ]
        for q in expo_qs:
            raw_data += f'[Q: {q}]\n' + serp(q, 8) + '\n\n'
            time.sleep(0.45)

        prompt = f"""K-뷰티 브랜드 Klear 전시회 리서처.
SerpApi 결과에서 미국 뷰티 전시회 6~8개 정리.

★ 오늘 날짜(검색 기준): {today_kor} ({today_iso})
★ 반드시 {today_iso} 이후 개최 예정 전시회만 — 이미 지난 것 절대 금지
★ date 필드: {today_iso} 이후 날짜만 입력 (이전 날짜 절대 금지)
★ 허용 연도: {yr}년 또는 {next_yr}년만 (이전 연도 데이터 무시)

⛔ 금지:
- 미국 이외 국가 전시회 (아시아·유럽·캐나다 등)
- 검색 결과에 없는 전시회 창작
- {today_iso} 이전 날짜 입력
- 미국 본토 좌표 범위 벗어남 (lat 24~50, lng -170~-60)

✅ 미국 주요 도시 좌표:
Las Vegas NV: 36.17,-115.14 / Los Angeles CA: 34.05,-118.24
New York NY: 40.71,-74.01 / Chicago IL: 41.88,-87.63
Anaheim CA: 33.84,-117.91 / Orlando FL: 28.54,-81.38
Nashville TN: 36.17,-86.78 / Miami FL: 25.77,-80.19
Atlanta GA: 33.75,-84.39 / Dallas TX: 32.78,-96.80

이메일: "@" 포함 확인된 것만 / 불확실 → "[[공식_웹사이트_확인]]"

[검색 데이터]
{raw_data[:8000]}

순수 JSON 배열만 출력 (마크다운 없이):
[
  {{
    "name": "전시회 공식명",
    "date": "{today_iso} 이후 날짜 예: {yr}-06-15 ~ 06-17",
    "city": "미국 도시명",
    "state": "주 약자",
    "lat": 위도숫자,
    "lng": 경도숫자,
    "theme": "주제 한국어 1줄",
    "attendees": 예상참가자수,
    "description": "특징 및 Klear 기대효과 한국어 2줄",
    "contact_email": "이메일 또는 [[공식_웹사이트_확인]]",
    "official_website": "https://확인된URL 또는 null",
    "booth_cost": "비용 또는 공식 사이트 문의",
    "tags": ["태그1","태그2","태그3"]
  }}
]"""

        res = ai.chat.completions.create(
            model='gpt-4o',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.15, max_tokens=4000,
        )
        txt = res.choices[0].message.content.strip()
        txt = _re.sub(r'^```(?:json)?\s*', '', txt, flags=_re.MULTILINE)
        txt = _re.sub(r'\s*```$',          '', txt, flags=_re.MULTILINE).strip()
        exhibitions = json.loads(txt)

        # ── 서버사이드 3중 필터 ─────────────────────────────────────────────
        valid = []
        for ex in exhibitions:
            lat, lng = float(ex.get('lat', 0)), float(ex.get('lng', 0))
            if 24 <= lat <= 50 and -170 <= lng <= -60:
                valid.append(ex)
            else:
                print(f'  ⚠ 비미국 좌표 제거: {ex.get("name","")} lat={lat} lng={lng}')
        exhibitions = valid

        future = []
        for ex in exhibitions:
            ds = str(ex.get('date', ''))
            m  = _re.search(r'(20\d\d)-(\d{2})-(\d{2})', ds)
            if m:
                try:
                    expo_dt = _dt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    if expo_dt >= today:
                        future.append(ex)
                    else:
                        print(f'  ⚠ 과거 날짜 제거: {ex.get("name","")} ({ds}) < {today_iso}')
                except Exception:
                    future.append(ex)
            else:
                ym = _re.search(r'(20\d\d)', ds)
                if ym and int(ym.group(1)) >= yr:
                    future.append(ex)
                elif not ym:
                    future.append(ex)
                else:
                    print(f'  ⚠ 과거 연도 제거: {ex.get("name","")} ({ds})')
        exhibitions = future

        exhibitions = [
            ex for ex in exhibitions
            if not _re.search(r'(20\d\d)', str(ex.get('date','')))
            or int((_re.search(r'(20\d\d)', str(ex.get('date',''))) or type('',(),{'group':lambda s,n:'0'})()).group(1)) >= yr
        ]

        print(f'✅ 전시회 {len(exhibitions)}개 ({today_iso} 기준 미래 개최분)')

        return jsonify({
            'success':     True,
            'exhibitions': exhibitions,
            'total':       len(exhibitions),
            'method':      f'SerpApi 10쿼리 × gpt-4o (기준일 {today_iso})',
            'analyzed_at': datetime.now().strftime('%Y-%m-%d %H:%M KST'),
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'전시회 수집 오류: {str(e)}'}), 500


@app.route('/api/outreach/channels', methods=['GET','POST'])
def api_outreach_channels():
    """실존 미국 뷰티 유통 채널 벤더 포털 직접 검색 (gpt-4o). 담당자 개인 이메일 절대 금지."""
    try:
        SERPAPI_KEY    = os.environ.get('SERPAPI_KEY', '')
        OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
        if not SERPAPI_KEY or not OPENAI_API_KEY:
            return jsonify({'error': '.env에 SERPAPI_KEY와 OPENAI_API_KEY를 설정하세요.'}), 400

        import urllib.request, urllib.parse, time
        from openai import OpenAI
        ai = OpenAI(api_key=OPENAI_API_KEY)

        def serp(query, num=6):
            params = urllib.parse.urlencode({
                'q': query, 'api_key': SERPAPI_KEY,
                'num': num, 'hl': 'en', 'gl': 'us',
            })
            try:
                with urllib.request.urlopen(
                    f'https://serpapi.com/search.json?{params}', timeout=18
                ) as r:
                    d = json.loads(r.read().decode())
                rows = d.get('organic_results', [])[:num]
                return '\n'.join(
                    f"[{i+1}] {row.get('title','')} | {row.get('link','')}\n    {row.get('snippet','')}"
                    for i, row in enumerate(rows)
                )
            except Exception as e:
                return f'(검색 오류: {e})'

        print('\n[Channel] 유통 채널 공식 벤더 포털 직접 검색...')
        raw_data = ''
        for q in [
            'Ulta Beauty new brand vendor submission portal site:ulta.com OR "newbrands@ulta.com"',
            'Sephora brand submission Accelerate program official email "brandsubmission@sephora.com" portal',
            'Target new vendor beauty brand application portal site:target.com suppliers',
            'Walmart supplier vendor application portal site:walmart.com OR site:one.walmart.com beauty brand',
            '"Soko Glam" brand wholesale submission official email "brands@sokoglam.com" contact',
            '"Glow Recipe" shop brand partner submission official email contact wholesale',
            'Amazon beauty brand vendor central seller central official portal contact email',
            '"Violet Grey" brand submission curate official contact email',
        ]:
            raw_data += f'[검색: {q}]\n' + serp(q, 6) + '\n\n'
            time.sleep(0.45)

        print('[Channel] OpenAI gpt-4o 정제...')
        prompt = f"""K-뷰티 브랜드 Klear 미국 유통 전략 리서처.
아래 SerpApi 검색 결과에서 실존 미국 뷰티 유통 채널 7~9개를 정리해줘.

절대 금지:
- 담당자 개인 이름 이메일 창작 금지 (emily.park@target.com 절대 금지)
- 없는 이메일 창작 금지

이메일/포털 규칙:
- "@" 포함 공식 이메일 확인 → buyer_email 그대로 사용
- 공식 벤더 포털 URL 확인 → vendor_portal_url 제공, buyer_email은 "[[공식_벤더_포털_확인]]"
- 둘 다 없으면 → buyer_email: "[[공식_웹사이트_확인]]"
- 대형 유통사는 반드시 공식 벤더 포털 URL 제공

[검색 데이터]
{raw_data[:7000]}

순수 JSON 배열만 출력:
[
  {{
    "name": "채널명",
    "type": "offline 또는 online",
    "icon": "이모지",
    "color": "#hex",
    "buyer_email": "공식이메일 또는 [[공식_벤더_포털_확인]] 또는 [[공식_웹사이트_확인]]",
    "vendor_portal_url": "공식 벤더 포털 URL",
    "official_website": "채널 공식 URL",
    "target_audience": "primary customer profile in English only (e.g., beauty-conscious women in their 20s-40s nationwide)",
    "commission": "수수료 구조",
    "moq": "최소 주문 수량",
    "notes": "K-뷰티 선세럼 입점 팁 2줄 (한국어)"
  }}
]"""

        res = ai.chat.completions.create(
            model='gpt-4o',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.15, max_tokens=3500,
        )
        raw = res.choices[0].message.content.strip().replace('```json','').replace('```','').strip()
        channels = json.loads(raw)
        print(f'유통 채널 {len(channels)}개 수집 완료')

        return jsonify({
            'success': True, 'channels': channels, 'total': len(channels),
            'method': 'SerpApi 직접검색 x OpenAI gpt-4o',
            'note': 'B2B 담당자 직접 이메일이 필요하면 Hunter.io 또는 Apollo.io API 연동을 권장합니다.',
            'analyzed_at': datetime.now().strftime('%Y-%m-%d %H:%M KST'),
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'채널 수집 오류: {str(e)}'}), 500


@app.route('/api/outreach/send-email', methods=['POST'])
def api_outreach_send_email():
    """이메일 발송 더미 라우터 (추후 SMTP 연동)"""
    try:
        data    = request.get_json(force=True) or {}
        to      = data.get('to', '')
        subject = data.get('subject', '')
        body    = data.get('body', '')
        type_   = data.get('type', 'general')

        print(f"\n📧 [OUTREACH EMAIL - {type_.upper()}]")
        print(f"   TO:      {to}")
        print(f"   SUBJECT: {subject[:60]}...")
        print(f"   BODY:    {len(body)} chars\n")

        return jsonify({
            "success": True,
            "message": f"{to}으로 발송이 완료되었습니다.",
            "type":    type_,
            "sent_at": datetime.now().strftime("%Y-%m-%d %H:%M KST"),
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500



# ═══════════════════════════════════════════════════════════════════════════
# 대시보드 시나리오 카드 (/api/generate-scenario)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/generate-scenario", methods=["POST"])
def api_generate_scenario():
    try:
        data            = request.get_json(force=True)
        scenario_key    = data.get("scenario_key", "").strip()
        product_name    = data.get("product_name", "").strip()
        target_audience = data.get("target_audience", "").strip()
        key_point       = data.get("key_point", "").strip()
        extra_option    = data.get("extra_option", "").strip()

        if not product_name:
            return jsonify({"success": False, "error": "제품명을 입력하세요."}), 400
        if scenario_key not in SCENARIO_PROMPTS:
            return jsonify({"success": False, "error": f"알 수 없는 시나리오: {scenario_key}"}), 400

        OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
        if not OPENAI_API_KEY:
            return jsonify({"success": False, "error": ".env에 OPENAI_API_KEY가 없습니다."}), 400

        system_prompt = SCENARIO_PROMPTS[scenario_key]["system"]
        extra_label   = SCENARIO_EXTRA_LABEL.get(extra_option, extra_option)
        user_prompt   = (
            f"📦 제품명: {product_name}\n"
            + (f"🎯 타겟 고객: {target_audience}\n" if target_audience else "")
            + (f"✨ 핵심 소구 포인트: {key_point}\n" if key_point else "")
            + (f"⚙️ 추가 옵션: {extra_label}\n" if extra_label else "")
            + "\n위 정보를 바탕으로 완성도 높은 콘텐츠를 작성해주세요."
        )

        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY, timeout=120.0)
        res = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.7, max_tokens=1400,
        )
        return jsonify({
            "success": True, "result": res.choices[0].message.content,
            "scenario_key": scenario_key,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M KST"),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# 멀티플랫폼 게시물 자동 작성 (/api/generate-multipost)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/generate-multipost", methods=["POST"])
def api_generate_multipost():
    try:
        data            = request.get_json(force=True)
        product_name    = data.get("product_name", "").strip()
        target_audience = data.get("target_audience", "").strip()
        key_point       = data.get("key_point", "").strip()
        goal            = data.get("goal", "awareness")
        platforms       = data.get("platforms", ["instagram"])
        media_list      = data.get("media", [])

        if not product_name:
            return jsonify({"success": False, "error": "제품명을 입력하세요."}), 400

        OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
        if not OPENAI_API_KEY:
            return jsonify({"success": False, "error": ".env에 OPENAI_API_KEY가 없습니다."}), 400

        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY, timeout=120.0)
        goal_label  = GOAL_LABELS.get(goal, goal)

        # Vision 분석
        vision_desc = ""
        vision_used = False
        valid_media = [m for m in media_list if m.get("base64")]
        if valid_media:
            vision_used = True
            vision_content = [{"type": "text", "text": (
                f"아래 {len(valid_media)}개의 이미지/동영상 썸네일을 분석해주세요.\n"
                f"제품명: {product_name}\n"
                "1. 주요 피사체 및 색감\n2. 제품 특징 및 분위기\n"
                "3. SNS 활용 가능한 시각적 포인트 2~3가지\n4. 플랫폼별 메인 컷 제안"
            )}]
            for m in valid_media[:4]:
                vision_content.append({"type": "image_url", "image_url": {
                    "url": f"data:{m.get('mediaType','image/jpeg')};base64,{m['base64']}",
                    "detail": "low"
                }})
            try:
                vis_res = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": vision_content}],
                    temperature=0.3, max_tokens=600,
                )
                vision_desc = vis_res.choices[0].message.content
            except Exception as ve:
                vision_desc = f"(Vision 분석 실패: {ve})"

        user_base = (
            f"📦 제품명: {product_name}\n"
            + (f"🎯 타겟 고객: {target_audience}\n" if target_audience else "")
            + (f"✨ 소구 포인트: {key_point}\n" if key_point else "")
            + f"🗓️ 캠페인 목적: {goal_label}\n"
            + (f"\n📷 첨부 미디어 분석:\n{vision_desc}\n" if vision_desc else "")
        )

        results = {}
        model   = "gpt-4o" if vision_used else "gpt-4-turbo-preview"
        for platform in platforms:
            system_prompt = PLATFORM_SYSTEM_PROMPTS.get(platform, "K-뷰티 SNS 마케터로 최적의 게시물을 작성하세요.")
            try:
                res = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_base},
                    ],
                    temperature=0.7, max_tokens=900,
                )
                results[platform] = res.choices[0].message.content
            except Exception as e:
                results[platform] = f"⚠️ 생성 오류: {e}"

        return jsonify({
            "success": True, "results": results,
            "vision_used": vision_used, "vision_desc": vision_desc,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M KST"),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# 광고 스토리보드 (/api/generate-storyboard)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/generate-storyboard", methods=["POST"])
def api_generate_storyboard():
    try:
        data            = request.get_json(force=True)
        product_name    = data.get("product_name", "").strip()
        target_audience = data.get("target_audience", "").strip()
        ad_format       = data.get("ad_format", "15sec")
        tone            = data.get("tone", "clean_minimal")
        message         = data.get("message", "").strip()
        scene_count     = int(data.get("scene_count", 4))
        custom_prompt   = data.get("custom_prompt", "").strip()

        if not product_name:
            return jsonify({"success": False, "error": "제품명을 입력하세요."}), 400

        OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
        if not OPENAI_API_KEY:
            return jsonify({"success": False, "error": ".env에 OPENAI_API_KEY가 없습니다."}), 400

        format_label = AD_FORMAT_LABELS.get(ad_format, ad_format)
        tone_label   = TONE_LABELS.get(tone, tone)
        scene_guide  = custom_prompt or (
            "각 씬마다: 비주얼, 나레이션/자막, 카메라, 감정 포인트를 포함하세요."
        )

        system_prompt = (
            "당신은 10년 경력의 K-뷰티 광고 크리에이티브 디렉터입니다.\n"
            "반드시 순수 JSON만 반환하세요.\n"
            "출력 스키마:\n"
            '{"concept":"...", "scenes":[{"title":"...","visual":"...","narration":"...","camera":"...","emotion":"..."}], "postproduction":"..."}'
        )
        user_prompt = (
            f"📦 제품명: {product_name}\n"
            + (f"🎯 타겟 고객: {target_audience}\n" if target_audience else "")
            + (f"💡 핵심 메시지: {message}\n" if message else "")
            + f"🎞️ 광고 포맷: {format_label}\n🎨 톤: {tone_label}\n🔢 씬 수: {scene_count}개\n\n"
            f"씬 작성 가이드:\n{scene_guide}\n\n스토리보드 JSON을 반환하세요."
        )

        from openai import OpenAI
        import re
        client = OpenAI(api_key=OPENAI_API_KEY, timeout=120.0)
        res = client.chat.completions.create(
            model="gpt-4-turbo-preview",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.6, max_tokens=2000,
            response_format={"type": "json_object"},
        )
        raw    = re.sub(r"^```json|```$", "", res.choices[0].message.content.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(raw)
        return jsonify({
            "success": True,
            "concept": parsed.get("concept", ""),
            "scenes":  parsed.get("scenes", []),
            "postproduction": parsed.get("postproduction", ""),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M KST"),
        })
    except json.JSONDecodeError as e:
        return jsonify({"success": False, "error": f"JSON 파싱 오류: {e}"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# AI 이미지 생성 (/api/generate-image) — HuggingFace SDXL
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/generate-image", methods=["POST"])
def api_generate_image():
    try:
        import requests as req_lib, base64 as _b64
        data         = request.get_json(force=True)
        platform     = data.get("platform", "instagram").strip()
        product_name = data.get("product_name", "K-beauty product").strip()
        post_text    = data.get("post_text", "").strip()
        style        = data.get("style", "photorealistic")

        HF_API_KEY = os.environ.get("HF_API_KEY", "")
        if not HF_API_KEY:
            return jsonify({"success": False,
                            "error": ".env에 HF_API_KEY가 없습니다. https://huggingface.co/settings/tokens 에서 발급하세요."}), 400

        style_label = {
            "photorealistic": "ultra-realistic product photo, hyperrealistic",
            "illustration":   "soft pastel watercolor illustration, artistic",
            "minimal":        "clean minimalist style, flat design, simple",
        }.get(style, "ultra-realistic product photo")

        width, height = HF_IMAGE_SIZE.get(platform, (1024, 1024))
        style_hint    = HF_STYLE_HINT.get(platform, "")

        product_info   = load_product_info(product_name)
        product_addon  = build_product_prompt_addon(product_info)
        product_source = product_info.get("source", "none")

        has_korean = lambda t: any('\uAC00' <= c <= '\uD7A3' for c in (t or ""))
        is_korean  = has_korean(product_name) or has_korean(post_text)

        if is_korean:
            product_name_final  = deepl_to_english(product_name)
            product_addon_final = deepl_to_english(product_addon) if product_addon else ""
            post_text_final     = deepl_to_english(post_text[:300]) if post_text else ""
            no_text = ", no text, no letters, no korean text, no asian characters, no writing, no watermark, no logo, pure product photography, high quality, 8k"
        else:
            product_name_final  = product_name
            product_addon_final = product_addon or ""
            post_text_final     = post_text[:300] if post_text else ""
            no_text = ", no text, no letters, no writing, no watermark, no logo, pure product photography, high quality, 8k"

        hf_prompt = (
            f"{style_label}, {product_name_final}, K-beauty skincare product, {style_hint}"
            + (f", {product_addon_final}" if product_addon_final else "")
            + no_text
        )

        OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
        if OPENAI_API_KEY and post_text_final:
            try:
                from openai import OpenAI
                oa = OpenAI(api_key=OPENAI_API_KEY, timeout=30.0)
                pr = oa.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": (
                        f"Write a Stable Diffusion prompt (max 80 words, English) for:\n"
                        f"Product: {product_name_final}\nPlatform: {platform}\n"
                        f"Style: {style_label}\nComposition: {style_hint}\n"
                        + (f"Product details: {product_addon_final}\n" if product_addon_final else "")
                        + f"Reference post: {post_text_final[:200]}\n"
                        f"Rules: NO text or typography. K-beauty aesthetic, product focused.\n"
                        f"Output: only the English image prompt."
                    )}],
                    temperature=0.4, max_tokens=120,
                )
                hf_prompt = pr.choices[0].message.content.strip() + no_text
            except Exception:
                pass

        api_url = "https://router.huggingface.co/hf-inference/models/stabilityai/stable-diffusion-xl-base-1.0"
        headers = {"Authorization": f"Bearer {HF_API_KEY}", "Content-Type": "application/json"}
        negative_prompt = (
            "text, letters, words, writing, watermark, logo, label, typography, "
            "font, alphabet, characters, blurry text, ugly, bad quality, deformed"
        )
        payload = {
            "inputs": hf_prompt,
            "parameters": {
                "width": width, "height": height,
                "num_inference_steps": 35, "guidance_scale": 7.5,
                "negative_prompt": negative_prompt,
            },
            "options": {"wait_for_model": True}
        }

        img_resp = req_lib.post(api_url, headers=headers, json=payload, timeout=120)
        if img_resp.status_code != 200:
            try:
                err_msg = img_resp.json().get("error", img_resp.text[:200])
            except Exception:
                err_msg = img_resp.text[:200]
            return jsonify({"success": False, "error": f"HF API 오류 ({img_resp.status_code}): {err_msg}"}), 500

        img_b64 = _b64.b64encode(img_resp.content).decode("utf-8")
        return jsonify({
            "success": True, "image_b64": img_b64,
            "imagen_prompt": hf_prompt, "platform": platform,
            "aspect": f"{width}x{height}",
            "product_source": product_source, "product_addon": product_addon,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M KST"),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# AI 영상 생성 (/api/generate-video) — Vertex AI Veo 2
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/generate-video", methods=["POST"])
def api_generate_video():
    try:
        import requests as req_lib, base64 as _b64
        data         = request.get_json(force=True)
        platform     = data.get("platform", "instagram").strip()
        product_name = data.get("product_name", "K-beauty product").strip()
        post_text    = data.get("post_text", "").strip()
        image_b64    = data.get("image_b64", "").strip()
        duration     = int(data.get("duration", 15))

        VERTEX_PROJECT    = os.environ.get("VERTEX_PROJECT_ID", "")
        VERTEX_LOCATION   = os.environ.get("VERTEX_LOCATION", "us-central1")
        VERTEX_CREDS_FILE = os.environ.get("VERTEX_CREDENTIALS_FILE", "")

        if not VERTEX_PROJECT:
            return jsonify({"success": False, "error": ".env에 VERTEX_PROJECT_ID가 없습니다."}), 400
        if not VERTEX_CREDS_FILE or not os.path.exists(VERTEX_CREDS_FILE):
            return jsonify({"success": False, "error": ".env에 VERTEX_CREDENTIALS_FILE 경로가 없거나 파일이 없습니다."}), 400
        if not image_b64:
            return jsonify({"success": False, "error": "image_b64가 필요합니다. 먼저 이미지를 생성하세요."}), 400

        product_name_en = deepl_to_english(product_name)
        post_text_en    = deepl_to_english(post_text[:300]) if post_text else ""

        motion_prompt = (
            f"15-second K-beauty product showcase commercial of {product_name_en}. "
            f"Scene 1 (0-4s): Elegant product reveal with soft bokeh background. "
            f"Scene 2 (4-9s): 360-degree product rotation, warm golden lighting. "
            f"Scene 3 (9-13s): Close-up of creamy texture, slow-motion formula drip. "
            f"Scene 4 (13-15s): Wide hero shot, cinematic fade-out. "
            f"Style: luxury K-beauty commercial, soft pastel palette, 4K cinematic quality."
        )

        OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
        if OPENAI_API_KEY and post_text_en:
            try:
                from openai import OpenAI
                oa = OpenAI(api_key=OPENAI_API_KEY, timeout=30.0)
                pr = oa.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": (
                        f"Write an optimized Vertex AI Veo 2 prompt for a 15-second K-beauty commercial (max 150 words, English).\n"
                        f"Product: {product_name_en}\nPlatform: {platform}\nReference: {post_text_en[:200]}\n"
                        f"Structure: 4 scenes (reveal→rotation→texture→hero shot).\nStyle: luxury beauty commercial.\n"
                        f"Output only the video prompt."
                    )}],
                    temperature=0.5, max_tokens=200,
                )
                motion_prompt = pr.choices[0].message.content.strip()
            except Exception:
                pass

        aspect = VEO_ASPECT.get(platform, "16:9")
        try:
            auth_headers = _vertex_auth_headers(VERTEX_CREDS_FILE)
        except Exception as e:
            return jsonify({"success": False, "error": f"Vertex AI 인증 실패: {e}"}), 500

        endpoint = (
            f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1/"
            f"projects/{VERTEX_PROJECT}/locations/{VERTEX_LOCATION}/"
            f"publishers/google/models/veo-2.0-generate-001:predictLongRunning"
        )
        payload = {
            "instances": [{"prompt": motion_prompt, "image": {
                "bytesBase64Encoded": image_b64, "mimeType": "image/png",
            }}],
            "parameters": {"aspectRatio": aspect, "durationSeconds": duration, "sampleCount": 1}
        }
        resp = req_lib.post(endpoint, headers=auth_headers, json=payload, timeout=60)
        if resp.status_code != 200:
            return jsonify({"success": False, "error": f"Vertex AI 오류 ({resp.status_code}): {resp.text[:300]}"}), 500

        return jsonify({
            "success": True,
            "operation_name": resp.json().get("name", ""),
            "motion_prompt": motion_prompt,
            "platform": platform, "aspect": aspect, "duration": duration,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/video-status/<path:operation_name>", methods=["GET"])
def api_video_status(operation_name):
    try:
        import requests as req_lib
        VERTEX_PROJECT    = os.environ.get("VERTEX_PROJECT_ID", "")
        VERTEX_LOCATION   = os.environ.get("VERTEX_LOCATION", "us-central1")
        VERTEX_CREDS_FILE = os.environ.get("VERTEX_CREDENTIALS_FILE", "")

        if not VERTEX_PROJECT or not VERTEX_CREDS_FILE:
            return jsonify({"success": False, "error": "Vertex AI 설정 없음"}), 400

        try:
            auth_headers = _vertex_auth_headers(VERTEX_CREDS_FILE)
        except Exception as e:
            return jsonify({"success": False, "error": f"인증 실패: {e}"}), 500

        if operation_name.startswith("projects/"):
            op_url = f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1/{operation_name}"
        else:
            op_url = f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT}/locations/{VERTEX_LOCATION}/operations/{operation_name}"

        resp = req_lib.get(op_url, headers=auth_headers, timeout=30)
        if resp.status_code != 200:
            return jsonify({"success": False, "error": f"상태 조회 실패 ({resp.status_code})"}), 500

        op_data = resp.json()
        if not op_data.get("done", False):
            return jsonify({"success": True, "status": "running", "video_b64": None})
        if "error" in op_data:
            return jsonify({"success": True, "status": "failed",
                            "error": op_data["error"].get("message", "알 수 없는 오류")})
        try:
            videos    = op_data["response"]["predictions"][0]["videos"]
            video_b64 = videos[0]["bytesBase64Encoded"]
            return jsonify({"success": True, "status": "succeeded", "video_b64": video_b64})
        except (KeyError, IndexError) as e:
            return jsonify({"success": True, "status": "failed", "error": f"영상 데이터 추출 실패: {e}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/debug-video", methods=["GET"])
def api_debug_video():
    """Vertex AI 설정 진단"""
    VERTEX_PROJECT    = os.environ.get("VERTEX_PROJECT_ID", "")
    VERTEX_LOCATION   = os.environ.get("VERTEX_LOCATION", "")
    VERTEX_CREDS_FILE = os.environ.get("VERTEX_CREDENTIALS_FILE", "")
    HF_API_KEY        = os.environ.get("HF_API_KEY", "")
    result = {
        "VERTEX_PROJECT_ID":       VERTEX_PROJECT    or "❌ 없음",
        "VERTEX_LOCATION":         VERTEX_LOCATION   or "❌ 없음",
        "VERTEX_CREDENTIALS_FILE": VERTEX_CREDS_FILE or "❌ 없음",
        "credentials_file_exists": "✅ 있음" if VERTEX_CREDS_FILE and os.path.exists(VERTEX_CREDS_FILE) else "❌ 없음",
        "HF_API_KEY":              "✅ 설정됨" if HF_API_KEY else "❌ 없음",
    }
    if VERTEX_CREDS_FILE and os.path.exists(VERTEX_CREDS_FILE):
        try:
            _vertex_auth_headers(VERTEX_CREDS_FILE)
            result["auth_token_test"] = "✅ 토큰 발급 성공"
        except Exception as e:
            result["auth_token_test"] = f"❌ 실패: {e}"
    from flask import make_response
    import json as _j
    resp = make_response(_j.dumps(result, ensure_ascii=False, indent=2))
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    return resp, 200


# ─────────────────────── 페이지 라우터 ───────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html', active_page='dashboard')

@app.route('/intelligence')
def intelligence():
    return render_template('intelligence.html', active_page='intelligence')

@app.route('/outreach')
def outreach():
    return render_template('outreach.html', active_page='outreach')

@app.route('/content')
def content():
    return render_template('content.html', active_page='content')

if __name__ == "__main__":
    print("\n🌿 Klear Intelligence Server 시작")
    print("   http://localhost:5000  ← 브라우저에서 열어주세요\n")
    app.run(debug=True, port=5000, threaded=True)