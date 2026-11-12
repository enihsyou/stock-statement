import type { components } from "./api-schema";
export type Session = components["schemas"]["SessionResponse"];
export type Report = components["schemas"]["ReportResponse"];
export type Detail = components["schemas"]["DetailResponse"];
export type Quote = components["schemas"]["QuoteResponse"];
export type Market = components["schemas"]["MarketResponse"];
export type Expectation = components["schemas"]["ExpectationData"];
export type Marker = components["schemas"]["MarkerData"];
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...init.headers },
  });
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ detail: "服务请求失败" }));
    throw new Error(body.detail || "服务请求失败");
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
export const shown = (value: string | null | undefined) => value ?? "—";
