import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';

// Custom Metrics
const runCreationDuration = new Trend('run_creation_duration_ms');
const leaseAcquireDuration = new Trend('lease_acquire_duration_ms');
const cacheHitRate = new Rate('mcp_cache_hit_rate');
const mcpCallsCounter = new Counter('mcp_logical_calls_total');

export const options = {
  scenarios: {
    studio_supervisors_50_vu: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 25 }, // Ramp up to 25 VUs
        { duration: '1m', target: 50 },  // Ramp up to 50 concurrent VUs
        { duration: '2m', target: 50 },  // Sustained load test at 50 VUs
        { duration: '30s', target: 0 },  // Ramp down
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    'http_req_duration': ['p(95)<300'],           // 95% of API requests return under 300ms
    'run_creation_duration_ms': ['p(95)<200'],   // Run creation under 200ms
    'mcp_cache_hit_rate': ['rate>0.85'],         // Cache hit ratio exceeds 85%
  },
};

const BASE_URL = __ENV.API_GATEWAY_URL || 'http://localhost:8000';
const MCP_URL = __ENV.MCP_GATEWAY_URL || 'http://localhost:8001';
const USERNAME = __ENV.APP_USERNAME || 'supervisor';
const PASSWORD = __ENV.APP_PASSWORD || 'shadow-protocol';

// Every service route is behind the operator login, so the run signs in once and
// every VU reuses the token. Without this the whole scenario measures nothing but
// the 401 path.
export function setup() {
  const res = http.post(
    `${BASE_URL}/auth/login`,
    JSON.stringify({ username: USERNAME, password: PASSWORD }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  if (res.status !== 200) {
    throw new Error(
      `Sign in failed (HTTP ${res.status}). Set APP_USERNAME and APP_PASSWORD to ` +
      'match the deployment before running the load test.'
    );
  }
  return { token: JSON.parse(res.body).token };
}

export default function (data) {
  const vuId = __VU;
  const sessionId = `sess-loadtest-vu-${vuId}`;
  const headers = {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${data.token}`,
  };

  // 1. Acquire Tenant Lease
  const leaseStart = Date.now();
  const leaseRes = http.post(
    `${BASE_URL}/leases/acquire`,
    JSON.stringify({ session_id: sessionId, user_id: `usr-loadtest-${vuId}` }),
    { headers: headers }
  );
  leaseAcquireDuration.add(Date.now() - leaseStart);

  check(leaseRes, {
    'lease acquired 200': (r) => r.status === 200,
    'has tenant_id': (r) => JSON.parse(r.body).tenant_id !== undefined,
  });

  const leaseData = JSON.parse(leaseRes.body);
  const tenantId = leaseData.tenant_id;

  // 2. Submit Investigation Run
  const runStart = Date.now();
  const runRes = http.post(
    `${BASE_URL}/runs`,
    JSON.stringify({
      tenant_id: tenantId,
      session_id: sessionId,
      user_id: `usr-loadtest-${vuId}`,
      objective: 'Will Shadow Protocol miss the 18:00 delivery deadline?',
    }),
    { headers: headers }
  );
  runCreationDuration.add(Date.now() - runStart);

  check(runRes, {
    'run created 200': (r) => r.status === 200,
    'status is QUEUED': (r) => JSON.parse(r.body).status === 'QUEUED',
  });

  // 3. Exercise MCP Gateway caching during wave
  const toolsToTest = [
    { tool_name: 'list_prometheus_metric_names', parameters: {} },
    { tool_name: 'query_prometheus', parameters: { query: 'render_queue_depth_frames' } },
    { tool_name: 'query_prometheus', parameters: { query: 'render_worker_gpu_utilization_ratio' } },
    { tool_name: 'query_loki_logs', parameters: { query: '{job="render"} |= "tile_size"' } },
  ];

  for (const tool of toolsToTest) {
    const mcpRes = http.post(
      `${MCP_URL}/call`,
      JSON.stringify({
        tool_name: tool.tool_name,
        parameters: tool.parameters,
        tenant_id: tenantId,
      }),
      { headers: headers }
    );

    mcpCallsCounter.add(1);
    if (mcpRes.status === 200) {
      const data = JSON.parse(mcpRes.body);
      cacheHitRate.add(data.cache_hit === true);
    }
    sleep(0.2);
  }

  // 4. Send lease heartbeat
  http.post(
    `${BASE_URL}/leases/heartbeat`,
    JSON.stringify({ tenant_id: tenantId, session_id: sessionId }),
    { headers: headers }
  );

  sleep(1.0);
}
