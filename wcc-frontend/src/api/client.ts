import axios from 'axios';

function redirectToLogin() {
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.href = `/api/v1/auth/login?next=${next}`;
}

function handleUnauthorized(err: unknown) {
  if (axios.isAxiosError(err) && err.response?.status === 401) {
    redirectToLogin();
  }
  return Promise.reject(err);
}

// The main app server (port 9000 in dev, same origin via nginx otherwise).
export const api = axios.create({
  baseURL: '/api/v1',
  headers: { 'Content-Type': 'application/json' },
});
api.interceptors.response.use((res) => res, handleUnauthorized);

// The WCC read-only dashboard backend. Both clients share the one
// wcc_session cookie the browser already attaches to same-origin requests
// -- there is no token to add here any more.
export const wccApi = axios.create({
  baseURL: '/api/wcc',
  headers: { 'Content-Type': 'application/json' },
});
wccApi.interceptors.response.use((res) => res, handleUnauthorized);
