import createClient from "openapi-fetch";
import type { paths } from "./schema";

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

export const api = createClient<paths>({ baseUrl: "", credentials: "include" });

api.use({
  onRequest({ request }) {
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method)) {
      request.headers.set("X-CSRFToken", csrfToken());
    }
    return request;
  },
});

export type { paths, components } from "./schema";
