const REPOSITORY_DISPATCH_URL = 'https://api.github.com/repos/ibottledo/CrewScheduler/dispatches';

function corsHeaders(request, env) {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '86400',
  };
}

function jsonResponse(body, status, request, env) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders(request, env),
      'Content-Type': 'application/json',
    },
  });
}

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders(request, env) });
    }

    if (request.method !== 'POST') {
      return jsonResponse({ error: 'POST 요청만 허용됩니다.' }, 405, request, env);
    }

    if (!env.GITHUB_TOKEN) {
      return jsonResponse({ error: 'Worker에 GITHUB_TOKEN이 설정되지 않았습니다.' }, 500, request, env);
    }

    try {
      const payload = await request.json();
      if (!payload || typeof payload !== 'object') {
        return jsonResponse({ error: '요청 데이터가 올바르지 않습니다.' }, 400, request, env);
      }

      const response = await fetch(REPOSITORY_DISPATCH_URL, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${env.GITHUB_TOKEN}`,
          'Accept': 'application/vnd.github+json',
          'Content-Type': 'application/json',
          'User-Agent': 'CrewScheduler-Cloudflare-Worker',
        },
        body: JSON.stringify({
          event_type: 'generate_schedule',
          client_payload: payload,
        }),
      });

      if (!response.ok) {
        return jsonResponse(
          { error: `GitHub 요청 실패 (상태 코드: ${response.status})` },
          response.status,
          request,
          env,
        );
      }

      return jsonResponse({ ok: true }, 202, request, env);
    } catch (error) {
      return jsonResponse({ error: '요청을 처리할 수 없습니다.' }, 400, request, env);
    }
  },
};
