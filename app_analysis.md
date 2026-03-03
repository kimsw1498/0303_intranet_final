# 마케팅 인트라넷 app.py 분석 리뷰

## 1. 시스템 개요

- **사용 프레임워크 및 핵심 라이브러리:** Flask, OpenAI Python SDK (`gpt-4-turbo-preview`, `gpt-4o-mini`), Apify Client, python-dotenv, urllib (표준), json/os (표준)
- **전체 아키텍처 요약:** "Klear K-Beauty Market Intelligence" 마케팅 인트라넷 서버. 외부 스크래핑 API(SerpApi, Apify)로 소셜 미디어 및 웹 데이터를 실시간 수집하고, OpenAI GPT-4 시리즈로 분석 후 JSON 응답을 반환하는 Flask 단일 서버 구조다. 대시보드/인텔리전스/아웃리치/콘텐츠 4개 페이지를 템플릿 렌더링으로 서빙하며, `.env` 파일의 API 키 유무에 따라 **하드코딩 샘플 데이터(데모 모드)**와 **실시간 AI 분석 모드**를 자동으로 구분하는 이중 레이어 설계다.

---

## 2. 주요 API 엔드포인트 및 기능 (표)

| HTTP Method | 엔드포인트 (경로) | 담당 기능 (비즈니스 로직) | 주요 파라미터/비고 |
|---|---|---|---|
| GET | `/` | 대시보드 페이지 렌더링 | `index.html` 서빙 |
| GET | `/intelligence` | 인텔리전스 페이지 렌더링 | `intelligence.html` 서빙 |
| GET | `/outreach` | 아웃리치 페이지 렌더링 | `outreach.html` 서빙 |
| GET | `/content` | 콘텐츠 페이지 렌더링 | `content.html` 서빙 |
| GET | `/api/intelligence` | 트렌드 샘플 데이터 반환 | 하드코딩 정적 데이터 (키워드, 플랫폼 통계, 스파크라인) |
| GET | `/api/outreach` | 인플루언서 아웃리치 샘플 반환 | 발송 현황, 열람/회신/대기 상태 포함 |
| GET | `/api/content` | 콘텐츠 시나리오 샘플 반환 | A/B 테스트 결과, 플랫폼별 시나리오 포함 |
| GET | `/api/all` | 위 3개 샘플 데이터 통합 반환 | intelligence + outreach + content 묶음 |
| POST | `/api/trend-keywords` | K-뷰티 트렌드 키워드 AI 분석 | `platform`, `category` / SerpApi → GPT-4 마크다운 보고서 |
| POST | `/api/market-entry` | 국가별 시장 진입 전략 분석 | `target_country`, `selected_categories` (4개 중 선택) / SerpApi → GPT-4 출처 인용 보고서 |
| POST | `/api/generate-content` | SNS 콘텐츠 초안 생성 | `type`, `product_name`, `target_audience`, `key_point`, `system_prompt`(선택) / GPT-4 |
| POST | `/api/trend/reddit` | Reddit 트렌드 키워드 TOP10 추출 | `APIFY_API_TOKEN` 필수 / r/AsianBeauty + r/SkincareAddiction 통합 수집 → gpt-4o-mini |
| POST | `/api/trend/youtube` | YouTube 트렌드 키워드 TOP10 추출 | `APIFY_API_TOKEN` 필수 / 3개 검색 쿼리로 영상 수집 → gpt-4o-mini |
| POST | `/api/trend/tiktok` | TikTok 트렌드 키워드 TOP10 추출 | `platform` 파라미터로 limit 조정 (전체=15, 단일=10) / Counter 해시태그 집계 → gpt-4o-mini |
| POST | `/api/trend/instagram` | Instagram 트렌드 키워드 TOP10 추출 | 캡션 정규식 파싱 + raw 태그 이중 수집 → gpt-4o-mini |
| POST | `/api/trend/all` | TikTok + Reddit 통합 TOP15 분석 | 두 플랫폼 동시 수집 후 단일 프롬프트로 통합 분석 / `source` 필드 (tiktok/reddit/both) 포함 |

---

## 3. 핵심 비즈니스 로직 흐름

- **소셜 플랫폼 트렌드 수집 파이프라인** (reddit/youtube/tiktok/instagram/all 공통): Apify 서드파티 스크래퍼 액터를 호출하여 게시물/영상 수집 → Python `Counter`로 해시태그 빈도 집계 및 `EXCLUDED_TAGS` 필터 적용 → 정제된 해시태그+캡션을 `PLATFORM_PROMPTS` 딕셔너리 템플릿에 주입 → gpt-4o-mini 분석 → `mentions` 내림차순 정렬된 JSON 배열 반환. 백엔드에서 한 번 더 정렬을 보장하는 이중 정렬 구조.

- **국가별 시장 진입 전략 분석** (`/api/market-entry`): 클라이언트로부터 `target_country` + `selected_categories` 수신 → `_fetch_serp_data_for_categories()`가 `CATEGORY_SEARCH_KEYWORDS` 매핑으로 카테고리별 최적화 쿼리를 구성해 SerpApi 구글 검색 수행 → `_CATEGORY_PROMPT` 딕셔너리로 선택 카테고리별 분석 지침을 동적 조합하여 system/user 프롬프트 빌드 → GPT-4-turbo가 각 근거 문장에 `[SOURCE N]` 인용 번호를 붙인 마크다운 보고서 생성.

- **SNS 콘텐츠 생성 엔진** (`/api/generate-content`): 클라이언트에서 전송한 `system_prompt`를 우선 적용 (없으면 기본 K-뷰티 마케터 페르소나 사용) → 제품명/타겟/소구 포인트를 user_prompt로 조합 → GPT-4-turbo가 콘텐츠 초안 생성. 클라이언트가 system_prompt를 직접 오버라이드할 수 있는 구조.

- **K-뷰티 트렌드 키워드 분석** (`/api/trend-keywords`): 플랫폼+카테고리 기반으로 SerpApi 구글 검색 실행 → 현재 주목 트렌드 / 급상승 키워드 / 브랜드 전략 시사점 3개 섹션의 마크다운 분석 반환. API 키 미설정 시 데모 모드 텍스트로 graceful 대응.

---

## 4. 리뷰어의 코멘트 (특이사항 및 유지보수 포인트)

- **클라이언트 프롬프트 오버라이드 보안 취약점:** `/api/generate-content`에서 클라이언트가 전송한 `system_prompt`를 별도 검증 없이 OpenAI system 역할로 직접 전달하는 구조 (app.py:411). 프롬프트 인젝션 공격에 노출되어 있으며, 악의적인 요청자가 서버의 AI 시스템 지침을 임의로 대체할 수 있다.

- **외부 API 3중 단일 장애점(SPOF):** SerpApi, Apify, OpenAI에 동기적으로 의존하며, SerpApi에만 10초 타임아웃이 설정되어 있고 Apify/OpenAI 호출에는 타임아웃 및 재시도 로직이 없다. 외부 서비스 장애 시 해당 엔드포인트 전체가 즉시 500 오류를 반환한다.

- **하드코딩 샘플 데이터와 실시간 데이터의 혼재:** `get_intelligence_data()`, `get_outreach_data()`, `get_content_data()` 함수에 하드코딩된 더미 데이터가 그대로 서빙되고 있어 (app.py:20~65), 대시보드 수치가 실제 분석 결과가 아닌 고정값임을 유지보수 시 주의해야 한다.
