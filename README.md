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