# [Feature Request] 인스타그램 탭 내 '실시간 트렌드 해시태그 추천' UI 추가 및 결과 영역 개선

## 1. 개요
현재 인스타그램 콘텐츠 생성 결과 영역 위에 **인스타그램 실시간 스크래핑 기반 트렌드 해시태그 추천 탭**을 추가합니다. 생성된 본문과 함께 현재 플랫폼에서 가장 바이럴되고 있는 핵심 키워드를 한눈에 파악하여 마케팅 효과를 극대화하는 것이 목적입니다. 또한, 생성된 결과물을 사용자가 직접 수정하고 손쉽게 되돌릴 수 있는 편의 기능을 추가합니다.

## 2. 위치 및 UI 레이아웃
* **위치:** 인스타그램 콘텐츠 생성 폼 하단, 그리고 실제 생성된 결과물(`<div id="result_area">`) 바로 **위(상단)**에 위치.
* **디자인 스타일:** 기존 트렌드 대시보드에서 사용 중인 순위형 리스트 UI(초록색 원형 배지 + 텍스트)를 가로 스크롤(또는 칩 형태)로 변형하여 공간 효율성을 높이거나, 카드 상단에 깔끔하게 배치.

## 3. 데이터 연동 및 비즈니스 로직
* **데이터 소스:** 인스타그램 스크래핑 API 결과값.
* **정렬 기준:** 스크래핑된 키워드 중 **언급량(mentions/volume)이 가장 많은 순**으로 내림차순 정렬하여 Top 10 노출.
* **인터랙션:** * **해시태그 다중 선택:** 추천된 해시태그 칩을 클릭하면 선택(Active) 상태로 토글되며, 여러 개의 해시태그를 동시 선택할 수 있습니다.
  * **선택 항목 추가:** `선택한 해시태그 추가` 버튼 클릭 시, 현재 선택된 해시태그들만 하단 결과 텍스트 영역의 'SEO 최적화 해시태그' 목록 끝에 자동으로 삽입됩니다.
  * **선택 초기화:** `선택 초기화` 버튼 클릭 시, 클릭해 둔 해시태그들의 선택 상태가 모두 해제됩니다.
  * **결과물 직접 수정 및 되돌리기:** '📱 인스타그램 생성 완료' 하단의 텍스트 박스를 사용자가 직접 클릭하여 자유롭게 내용을 수정할 수 있도록 허용합니다. 또한, `되돌리기` 버튼을 클릭하면 사용자가 수정한 내역을 취소하고 AI가 최초 생성했던 원본 상태로 복구합니다.

## 4. 예상 HTML 구조 (참고용 Mockup)

```html
<div id="instagram-hashtag-recommendation" style="margin-bottom: 20px; max-width: 800px; background: white; padding: 20px; border-radius: 10px; box-shadow: rgba(0, 0, 0, 0.05) 0px 4px 6px; border-left: 5px solid #E1306C;">
    <h4 style="margin-top: 0; color: #E1306C; font-size: 16px;">🔥 인스타그램 실시간 트렌드 해시태그 (언급량 순)</h4>
    
    <div style="display: flex; flex-wrap: wrap; gap: 10px; margin-top: 15px;" id="hashtag-container">
        <div class="hashtag-chip" style="display: flex; align-items: center; gap: 8px; background: #f9f9f9; padding: 6px 12px; border-radius: 20px; border: 1px solid #eee; font-size: 13.5px; cursor: pointer;">
            <span style="min-width: 18px; height: 18px; background: #E1306C; color: #fff; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700;">1</span>
            <span style="font-weight: 600; color: #333;">glass skin</span>
        </div>
        <div class="hashtag-chip selected" style="display: flex; align-items: center; gap: 8px; background: #ffe8f0; padding: 6px 12px; border-radius: 20px; border: 1px solid #E1306C; font-size: 13.5px; cursor: pointer;">
            <span style="min-width: 18px; height: 18px; background: #E1306C; color: #fff; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700;">2</span>
            <span style="font-weight: 600; color: #E1306C;">korean beauty</span>
        </div>
    </div>

    <div style="margin-top: 15px; display: flex; gap: 10px;">
        <button onclick="addSelectedHashtags()" style="background-color: #E1306C; color: white; border: none; padding: 8px 15px; border-radius: 5px; cursor: pointer; font-size: 13px; font-weight: bold;">
            ➕ 선택한 해시태그 추가
        </button>
        <button onclick="resetHashtagSelection()" style="background-color: #f1f1f1; color: #555; border: 1px solid #ccc; padding: 8px 15px; border-radius: 5px; cursor: pointer; font-size: 13px;">
            🔄 선택 초기화
        </button>
    </div>
</div>

<div id="result_area" style="display: block; max-width: 800px; background: white; padding: 25px; border-radius: 10px; box-shadow: rgba(0, 0, 0, 0.05) 0px 4px 6px; border-left: 5px solid rgb(225, 48, 108);">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
        <h3 style="margin: 0; color: #E1306C;">📱 인스타그램 생성 완료</h3>
        <button onclick="undoTextChanges()" style="background-color: transparent; color: #666; border: 1px solid #ddd; padding: 6px 12px; border-radius: 5px; cursor: pointer; font-size: 12px; display: flex; align-items: center; gap: 5px;">
            ↩️ 원본 되돌리기
        </button>
    </div>
    
    <pre id="result_text" contenteditable="true" style="white-space: pre-wrap; font-family: 'Pretendard', sans-serif; line-height: 1.6; background: #f9f9f9; padding: 20px; border-radius: 8px; border: 1px solid #eee; margin-bottom: 20px; outline: none; min-height: 100px;">...</pre>
    
    <button onclick="sendToN8n()" style="background-color: #E1306C; color: white; border: none; padding: 12px 20px; border-radius: 5px; cursor: pointer; font-weight: bold; font-size: 15px; width: 100%;">
        📸 인스타그램으로 자동 업로드 (n8n 연동)
    </button>
</div>


## 5. 작업 지시서 (Claude를 위한 구현 가이드)
위 기획 명세서를 바탕으로 작업 환경의 파일들을 수정하여 기능을 완성해줘. 

### 🛠️ Step 1: Backend (`app.py`) 수정
* 현재 인스타그램 트렌드 데이터를 가져오는 API 엔드포인트(예: `/api/trend/instagram`)가 올바르게 Top 10 해시태그 목록을 반환하는지 확인하고, 프론트엔드에서 쓰기 좋은 형태(JSON 배열 등)로 내려주도록 연결해줘.
* (선택) 만약 콘텐츠 생성 API(`/api/generate-content`)를 호출할 때 트렌드 해시태그도 함께 내려주는 것이 구조상 더 효율적이라면 그 방식으로 응답을 수정해도 좋아. 현재 프로젝트 구조에 가장 알맞은 방식으로 데이터를 공급해줘.

### 🎨 Step 2: Frontend HTML (`templates/content.html` 등) 수정
* 인스타그램 생성 탭 내의 결과 영역(`<div id="result_area">`) 주변에 내가 위에 작성한 **HTML Mockup 구조**를 반영해줘.
* `<pre id="result_text">`에 `contenteditable="true"` 속성을 추가하고, **원본 되돌리기** 버튼 등 새로 추가된 요소들을 알맞은 위치에 배치해줘.
* 해시태그 칩(Chip)이 클릭될 때 선택된 상태(`.selected`)와 기본 상태가 시각적으로 구분되도록 CSS (인라인 또는 스타일 태그)를 구성해줘.

### ⚙️ Step 3: Frontend JavaScript 로직 구현
HTML 하단 스크립트 영역이나 연결된 JS 파일에 다음 핵심 로직들을 구현해줘:
1. **해시태그 동적 렌더링 & 토글**: 백엔드에서 받아온 해시태그 데이터를 기반으로 칩 요소를 동적으로 생성하고, 클릭 시 선택(Active) 상태가 토글되게 만들어줘.
2. `addSelectedHashtags()`: 현재 선택된 해시태그들의 텍스트만 추출해서, `#result_text` 내부 텍스트 맨 아래에 ` #해시태그1 #해시태그2` 형태로 부드럽게 추가되도록 처리해줘.
3. `resetHashtagSelection()`: 선택된 모든 해시태그 칩의 상태를 초기화(선택 해제) 해줘.
4. **원본 저장 및 `undoTextChanges()`**: API를 통해 콘텐츠가 처음 생성되어 화면에 뿌려질 때, 그 **초기 원본 텍스트**를 별도의 자바스크립트 변수(예: `originalGeneratedText`)에 저장해둬. 그리고 사용자가 되돌리기 버튼을 누르면 그 변수값을 불러와서 수정한 내용을 원상복구시켜줘.

내가 제시한 요구사항과 기존 코드의 맥락을 잘 파악해서, 버그 없이 한 번에 동작하는 코드를 제안해줘.