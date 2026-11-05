export function apiError(error: unknown, fallback = "No se ha podido completar la operación."): string {
  if (error && typeof error === "object" && "detail" in error && typeof error.detail === "string") return error.detail;
  return fallback;
}
