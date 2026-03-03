### 💡 추가 요구사항: UX 개선 및 백그라운드 처리 (Frontend & Backend 통합 수정)

**1. 해시태그 '추가 완료' 팝업창 구현**
* `addSelectedHashtags()` 함수가 실행되어 텍스트 영역에 해시태그가 성공적으로 삽입되면, 사용자에게 **"추가 완료!"**라는 알림창을 띄워줘.
* 브라우저 기본 `alert()`를 피하고, 화면 우측 하단이나 상단에 부드럽게 나타났다가 사라지는 깔끔한 **Toast 팝업 UI (CSS + JS)** 형태로 구현해줘.

**2. 페이지 이동(Tab 전환) 시 콘텐츠 백그라운드 생성 유지 (중요 ⭐)**
현재 동기식(Synchronous)으로 동작하는 콘텐츠 생성 로직을 **비동기식(Asynchronous) + 폴링(Polling)** 구조로 변경해야 해. 사용자가 다른 탭(예: `/intelligence`)으로 이동하더라도 생성이 취소되지 않고 계속 진행되도록 **백엔드와 프론트엔드 모두** 아래 지침에 따라 수정해줘.

#### 🛠️ Backend 수정 지침 (`app.py`)
* **Task 매니저 구현:** 무거운 Celery 대신, Python 내장 `threading`과 전역 딕셔너리(예: `tasks = {}`)를 활용한 간단한 백그라운드 작업 큐를 만들어줘.
* **생성 엔드포인트 수정 (`/api/generate-content` 등):** 요청이 들어오면 고유한 `task_id`(UUID)를 생성하고, 스레드를 띄워 GPT 생성을 시작한 뒤, 클라이언트에게 즉시 `{"task_id": "...", "status": "processing"}` (HTTP 202)를 반환하도록 수정해.
* **상태 확인 엔드포인트 추가 (`/api/task-status/<task_id>`):** 프론트엔드에서 주기적으로 작업 완료 여부를 확인할 수 있는 새로운 API를 만들어줘. 완료 시 최종 생성된 텍스트 결과를 반환해야 해.

#### 🎨 Frontend 공통 수정 지침 (`templates/layout.html`)
* 모든 페이지에서 상태를 추적할 수 있도록 `layout.html`에 전역 로딩 UI(예: 우측 하단 "⏳ 콘텐츠 생성 중...")를 추가해줘.
* `layout.html` 하단 스크립트에서 페이지 로드 시 `localStorage.getItem('generatingTaskId')`를 확인해. 값이 있다면 `/api/task-status/<task_id>`를 주기적(예: 3초마다)으로 호출(Polling)하는 로직을 작성해줘.
* 폴링 결과 작업이 완료되었다면 전역 로딩 UI를 끄고, "✨ 콘텐츠 생성이 완료되었습니다! 콘텐츠 탭을 확인하세요."라는 Toast 알림을 띄워줘.

#### 🎨 Frontend 개별 수정 지침 (`templates/content.html`)
* 콘텐츠 생성 버튼을 누르면, 폼 제출 후 `task_id`를 응답받아 `localStorage.setItem('generatingTaskId', taskId)`로 저장해.
* 현재 화면이 `content.html`인 상태에서 폴링이 완료되면, 즉시 `<pre id="result_text">` 영역에 결과물을 렌더링하고 `localStorage`를 비우는(`removeItem`) 로직을 연결해줘.

이 요구사항들을 만족하도록 `app.py`, `layout.html`, `content.html` 각각에 들어가야 할 전체 수정 코드를 단계별로 명확하게 작성해줘.

### 💡 추가 요구사항: UX 개선 및 백그라운드 처리 (Frontend & Backend 통합 수정)

제공된 HTML 파일들(`layout.html`, `content.html`)과 Flask 아키텍처를 바탕으로 아래 두 가지 기능을 구현해줘.

**1. 전역 Toast 팝업 UI 구현 (layout.html)**
* 브라우저 기본 `alert()` 대신, 화면 우측 하단이나 상단에 부드럽게 나타났다가 사라지는 깔끔한 **Toast 팝업 UI (CSS + JS)**를 구현해줘.
* 이 Toast 기능은 전역에서 사용할 수 있도록 `layout.html`에 `showToast(message, type)` 형태의 함수로 정의해줘.
* `content.html`의 `addSelectedHashtags()` 함수가 성공적으로 실행되면 `showToast("✅ 해시태그 추가 완료!")`가 호출되게 수정해줘.

**2. 페이지 이동(Tab 전환) 시 콘텐츠 백그라운드 생성 유지 (중요 ⭐)**
현재 동기식으로 동작하는 콘텐츠 생성 로직을 **비동기식(Asynchronous) + 폴링(Polling)** 구조로 변경해야 해. 사용자가 다른 탭으로 이동하더라도 생성이 취소되지 않도록 아래 지침에 따라 수정해.

#### 🛠️ Backend 수정 지침 (`app.py`)
* **Task 매니저 구현:** Python 내장 `threading`과 전역 딕셔너리(예: `tasks = {}`)를 활용한 간단한 백그라운드 작업 큐를 만들어줘.
* **생성 엔드포인트 수정 (`/api/generate-content`):** 요청이 들어오면 고유한 `task_id`(UUID)를 생성하고, 스레드를 띄워 GPT 생성을 시작한 뒤, 즉시 `{"task_id": "...", "status": "processing"}` (HTTP 202 응답)를 반환하도록 수정해.
* **상태 확인 엔드포인트 추가 (`/api/task-status/<task_id>`):** 작업 상태를 폴링할 수 있는 API를 만들어줘. 완료 시 최종 생성된 텍스트(`result`)를 반환해야 해.

#### 🎨 Frontend 공통 수정 지침 (`templates/layout.html`)
* 모든 페이지에서 상태를 추적할 수 있도록, `localStorage.getItem('generatingTaskId')` 값이 있다면 `/api/task-status/<task_id>`를 주기적(예: 3초마다)으로 호출(Polling)하는 글로벌 로직을 `layout.html` 하단 스크립트에 작성해줘.
* 폴링 중일 때는 우측 하단에 "⏳ 콘텐츠 생성 중..." 이라는 미니 플로팅 인디케이터를 띄워줘.
* 폴링 결과 작업이 완료되었다면:
  1. 인디케이터를 숨기고, `localStorage`를 비워(`removeItem`).
  2. 전역 Toast로 `"✨ 콘텐츠 생성이 완료되었습니다!"` 알림을 띄워줘.
  3. **중요:** `window.dispatchEvent(new CustomEvent('contentGenerated', { detail: data }))` 를 발생시켜서, 현재 열려있는 페이지가 이벤트를 감지할 수 있게 해줘.

#### 🎨 Frontend 개별 수정 지침 (`templates/content.html`)
* `generateInstaPost()` 함수를 수정해서, API 호출 후 `task_id`를 응답받으면 `localStorage.setItem('generatingTaskId', taskId)`로 저장하고 버튼을 로딩 상태로 변경해줘. (폴링 자체는 layout.html에 맡김)
* `window.addEventListener('contentGenerated', (e) => { ... })` 이벤트 리스너를 추가해.
* 이 리스너가 트리거되면, 전달받은 `e.detail.result` 값을 `<pre id="result_text">`에 렌더링하고, 원본 텍스트 변수(`originalGeneratedText`)를 업데이트한 뒤, 버튼 상태를 복구하고 결과 영역(`result_area`)을 화면에 표시하는 로직을 작성해줘. 해시태그 로드(`loadInstagramHashtags()`)도 이때 실행되게 해줘.

이 구조가 꼬이지 않고 완벽하게 동작하도록 `app.py`, `layout.html`, `content.html` 각각의 수정된 코드를 명확히 제시해줘.

다한 후 시뮬레이션 돌리고 오류 없는지 파악하고 작업 끝내