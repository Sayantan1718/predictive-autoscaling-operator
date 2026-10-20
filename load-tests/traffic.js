import http from 'k6/http';
import { check } from 'k6';

const profile = __ENV.PROFILE || 'ramp';
const peakRps = Number(__ENV.PEAK_RPS || 30);
const hourSeconds = Number(__ENV.HOUR_SECONDS || 60);
const targetUrl = __ENV.TARGET_URL || 'http://localhost:8080/';

// share of peak traffic for each hour of a made-up day
const dayShape = [
  0.10, 0.08, 0.07, 0.07, 0.08, 0.12, 0.20, 0.40,
  0.70, 0.85, 0.90, 0.95, 0.90, 0.85, 0.80, 0.80,
  0.85, 0.90, 0.70, 0.50, 0.35, 0.25, 0.18, 0.12,
];

function rampStages() {
  return [
    { target: 2, duration: '2m' },
    { target: peakRps, duration: '8m' },
    { target: peakRps, duration: '3m' },
    { target: 2, duration: '2m' },
  ];
}

function dailyStages() {
  return dayShape.map((share) => ({
    target: Math.max(1, Math.round(peakRps * share)),
    duration: `${hourSeconds}s`,
  }));
}

const stages = profile === 'daily' ? dailyStages() : rampStages();

export const options = {
  scenarios: {
    traffic: {
      executor: 'ramping-arrival-rate',
      startRate: 2,
      timeUnit: '1s',
      preAllocatedVUs: 20,
      maxVUs: 200,
      stages,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  const res = http.get(targetUrl);
  check(res, { 'status is 200': (r) => r.status === 200 });
}