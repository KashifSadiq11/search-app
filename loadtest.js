/**
 * k6 Load Test Script for Recommendation Engine
 *
 * Usage:
 *   k6 run --vus 10 --duration 30s loadtest.js
 *   k6 run --vus 50 --duration 5m loadtest.js
 *
 * Install k6: https://k6.io/docs/getting-started/installation/
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const searchLatency = new Trend('search_latency', true);
const zeroResultRate = new Rate('zero_results');

// Configuration
const BASE_URL = __ENV.BASE_URL || 'http://localhost:8016';

// Sample queries for realistic testing
const QUERIES = [
  'office bag',
  'leather wallet',
  'running shoes',
  'wireless headphones',
  'laptop stand',
  'water bottle',
  'backpack',
  'phone case',
  'desk lamp',
  'notebook',
  'pen',
  'hoodie',
  'jacket',
  'watch',
  'sunglasses',
];

export const options = {
  // Default test configuration
  stages: [
    { duration: '30s', target: 10 },  // Ramp up
    { duration: '1m', target: 10 },   // Steady state
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'], // 95% of requests should complete within 500ms
    errors: ['rate<0.1'],              // Error rate should be below 10%
  },
};

// Helper to get random query
function getRandomQuery() {
  return QUERIES[Math.floor(Math.random() * QUERIES.length)];
}

// Helper to get random user ID
function getRandomUserId() {
  return `user_${Math.floor(Math.random() * 1000)}`;
}

export default function () {
  // Randomly choose between different endpoints
  const scenario = Math.random();

  if (scenario < 0.6) {
    // 60% - Semantic search
    testSemanticSearch();
  } else if (scenario < 0.8) {
    // 20% - Popular items
    testPopularItems();
  } else if (scenario < 0.95) {
    // 15% - Health check
    testHealthCheck();
  } else {
    // 5% - Recommendations
    testRecommendations();
  }

  // Random sleep between requests (100-500ms)
  sleep(0.1 + Math.random() * 0.4);
}

function testSemanticSearch() {
  const query = getRandomQuery();
  const payload = JSON.stringify({
    query: query,
    limit: 20,
    offset: 0,
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
      'X-Request-ID': `loadtest-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    },
  };

  const start = Date.now();
  const response = http.post(`${BASE_URL}/search/semantic/`, payload, params);
  const duration = Date.now() - start;

  searchLatency.add(duration);

  const success = check(response, {
    'semantic search status is 200': (r) => r.status === 200,
    'semantic search has items': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.items !== undefined;
      } catch {
        return false;
      }
    },
  });

  errorRate.add(!success);

  // Check for zero results
  try {
    const body = JSON.parse(response.body);
    zeroResultRate.add(body.items && body.items.length === 0);
  } catch {
    // Ignore parse errors
  }
}

function testPopularItems() {
  const params = {
    headers: {
      'X-Request-ID': `loadtest-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    },
  };

  const response = http.get(`${BASE_URL}/popular/?limit=20`, params);

  const success = check(response, {
    'popular items status is 200': (r) => r.status === 200,
  });

  errorRate.add(!success);
}

function testHealthCheck() {
  const response = http.get(`${BASE_URL}/health`);

  check(response, {
    'health check status is 200': (r) => r.status === 200,
    'health check is healthy': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.status === 'healthy';
      } catch {
        return false;
      }
    },
  });
}

function testRecommendations() {
  const userId = getRandomUserId();
  const payload = JSON.stringify({
    user_id: userId,
    algorithm: 'auto',
    limit: 10,
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
      'X-Request-ID': `loadtest-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    },
  };

  const response = http.post(`${BASE_URL}/recommend/enhanced/`, payload, params);

  const success = check(response, {
    'recommendations status is 200': (r) => r.status === 200,
  });

  errorRate.add(!success);
}

// Summary handler for pretty output
export function handleSummary(data) {
  return {
    stdout: textSummary(data, { indent: '  ', enableColors: true }),
  };
}

function textSummary(data, options) {
  const metrics = data.metrics;

  let output = '\n';
  output += '='.repeat(60) + '\n';
  output += 'LOAD TEST SUMMARY\n';
  output += '='.repeat(60) + '\n\n';

  // Request metrics
  if (metrics.http_reqs) {
    output += `Total Requests: ${metrics.http_reqs.values.count}\n`;
    output += `Request Rate: ${(metrics.http_reqs.values.rate || 0).toFixed(2)} req/s\n\n`;
  }

  // Latency metrics
  if (metrics.http_req_duration) {
    output += 'Latency:\n';
    output += `  p50: ${(metrics.http_req_duration.values['p(50)'] || 0).toFixed(2)} ms\n`;
    output += `  p95: ${(metrics.http_req_duration.values['p(95)'] || 0).toFixed(2)} ms\n`;
    output += `  p99: ${(metrics.http_req_duration.values['p(99)'] || 0).toFixed(2)} ms\n`;
    output += `  avg: ${(metrics.http_req_duration.values.avg || 0).toFixed(2)} ms\n\n`;
  }

  // Custom metrics
  if (metrics.search_latency) {
    output += 'Search Latency:\n';
    output += `  p50: ${(metrics.search_latency.values['p(50)'] || 0).toFixed(2)} ms\n`;
    output += `  p95: ${(metrics.search_latency.values['p(95)'] || 0).toFixed(2)} ms\n\n`;
  }

  if (metrics.errors) {
    output += `Error Rate: ${((metrics.errors.values.rate || 0) * 100).toFixed(2)}%\n`;
  }

  if (metrics.zero_results) {
    output += `Zero Result Rate: ${((metrics.zero_results.values.rate || 0) * 100).toFixed(2)}%\n`;
  }

  output += '\n' + '='.repeat(60) + '\n';

  return output;
}
