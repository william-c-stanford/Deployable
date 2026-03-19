import axios from "axios";
import { useAuthStore } from "@/stores/authStore";

const api = axios.create({
  baseURL: "/api",
  headers: {
    "Content-Type": "application/json",
  },
});

api.interceptors.request.use((config) => {
  const { token, role, userId } = useAuthStore.getState();

  // Prefer JWT Bearer token
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }

  // Always include demo headers as fallback for role-based scoping
  if (role) {
    config.headers["X-Demo-Role"] = role;
  }
  if (userId) {
    config.headers["X-Demo-User-Id"] = userId;
  }

  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      useAuthStore.getState().logout();
      window.location.href = "/";
    }
    return Promise.reject(error);
  },
);

export default api;
