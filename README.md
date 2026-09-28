# CrewScheduler

[스케줄러 열기](https://ibottledo.github.io/CrewScheduler)

10명의 직원을 대상으로 월간 D(주간), E(저녁), N(야간) 근무표를 자동 생성합니다. 웹페이지에서 PAT를 입력하지 않고 바로 사용할 수 있습니다.

## 사용 방법

### 1. 연도와 월 선택

생성할 연도와 월을 선택합니다. 월을 바꾸면 해당 월의 날짜 수에 맞춰 표가 다시 표시됩니다.

### 2. 직원별 설정

`설정할 인원 선택`에서 직원을 고른 뒤 설정합니다.

- `크루 휴식 기간`: 크루로 근무하지 않을 기간을 시작일과 종료일로 입력합니다.
- 기간을 비워두면 해당 직원은 크루로 배정되지 않습니다.
- `선호 근무 비율`: 원하는 D:E:N 비율을 입력합니다. 기본값은 `2:1:2`입니다.

직원을 바꿀 때마다 선택한 직원의 설정이 표시됩니다. 입력값은 현재 페이지를 열어 둔 동안 유지됩니다.

### 3. 전체 Crew 선택

고정 근무표의 `전체 Crew` 체크박스를 선택하면 해당 직원은 월 전체가 Crew 기간으로 처리됩니다. 휴식 기간을 따로 입력한 경우에는 전체 Crew 설정이 우선합니다.

### 4. 사전 고정 근무표 입력

직원별 날짜 셀을 클릭해 다음 순서로 값을 바꿉니다.

```text
빈칸 -> 휴가 -> OFF -> D -> E -> N -> 빈칸
```

- `D`, `E`, `N`: 해당 날짜의 근무를 고정합니다.
- `OFF`: 해당 날짜를 휴무로 고정합니다.
- `휴가`: 근무에서 제외하고 평균 계산에서도 제외합니다.
- 같은 날짜와 그룹에 고정 근무자가 중복되면 스케줄 생성이 중단됩니다.

### 5. 스케줄 생성

`솔버 제한 시간`을 초 단위로 입력한 뒤 `근무표 생성`을 클릭합니다.

요청이 접수되면 GitHub Actions에서 스케줄을 계산합니다. 보통 약 30초 후 결과가 준비됩니다. 복잡한 조건에서는 더 오래 걸릴 수 있습니다.

### 6. 결과 확인

약 30초 뒤 `근무표 가져오기`를 클릭합니다.

- `D`, `E`, `N`: 근무 유형
- `-`: 일반 휴무
- `V`: 휴가
- `평균(주)`: 주간 평균 근무시간
- `비율(D:E:N)`: 실제 배정된 근무 횟수

## 운영자 설정

이미 배포된 사이트를 사용하는 일반 사용자는 이 단계가 필요 없습니다.

### Cloudflare Worker

브라우저가 GitHub API를 직접 호출하지 않도록 Cloudflare Worker가 중계합니다. Worker의 `GITHUB_TOKEN`은 반드시 Secret으로 등록해야 합니다.

1. Cloudflare에서 `worker.js`를 Worker로 배포합니다.
2. Worker 설정의 `Variables and Secrets`에서 환경 `Production`을 선택합니다.
3. Secret을 추가합니다.
	- Key: `GITHUB_TOKEN`
	- Value: GitHub 저장소에 접근할 수 있는 PAT
4. `index.html`의 `SCHEDULER_API_URL`을 Worker 주소로 설정합니다.

PAT는 `index.html`이나 `worker.js`에 직접 작성하지 않습니다.

로컬에서 Wrangler를 사용하는 경우:

```bash
npx wrangler login
npx wrangler secret put GITHUB_TOKEN
npx wrangler deploy
```

### GitHub Actions

`.github/workflows/remote_schedule.yaml`이 `repository_dispatch` 요청을 받아 다음 작업을 수행합니다.

1. 요청 데이터 저장
2. Python과 OR-Tools 설치
3. `main.py`로 스케줄 계산
4. `schedule_result.json` 커밋 및 push

## 문제 해결

### "요청 실패"가 표시되는 경우

- Worker에 `GITHUB_TOKEN` Secret이 등록되어 있는지 확인합니다.
- Secret이 `Production` 환경에 등록되어 있는지 확인합니다.
- GitHub PAT가 만료되지 않았는지 확인합니다.
- PAT에 저장소 Contents 쓰기 권한이 있는지 확인합니다.

### 결과가 아직 없다고 표시되는 경우

스케줄 생성 요청 직후에는 결과 파일이 아직 만들어지지 않았을 수 있습니다. 30초 정도 기다린 뒤 다시 `근무표 가져오기`를 클릭합니다.

### 고정 입력 충돌이 표시되는 경우

오류 메시지에 표시된 날짜와 직원을 확인해 중복된 D/N/E 근무, 금지된 연속 근무, 휴가와 근무의 중복 입력을 수정합니다.

## 참고

현재 결과는 저장소의 `schedule_result.json` 하나에 저장됩니다. 여러 사람이 동시에 생성 요청을 보내면 마지막으로 완료된 요청의 결과가 표시됩니다.
# [CrewScheduler](https://ibottledo.github.io/CrewScheduler)

## 공개 사용 설정

웹페이지에서 PAT를 입력하지 않고 사용하려면 Cloudflare Worker를 배포합니다.

1. Cloudflare 대시보드에서 Worker를 만들거나 로컬에서 Wrangler를 설치합니다.
2. 이 저장소에서 `wrangler deploy`를 실행합니다.
3. Worker Secret에 GitHub 저장소 권한이 있는 토큰을 등록합니다.

```bash
npx wrangler login
npx wrangler secret put GITHUB_TOKEN
npx wrangler deploy
```

배포 후 발급된 `https://...workers.dev` 주소를 `index.html`의 `SCHEDULER_API_URL`에 넣고 배포하면 됩니다. 토큰은 `worker.js`를 포함한 프론트 코드에 넣지 않습니다.