import { test, expect, type Page } from "@playwright/test";
import type { components } from "../../../packages/api-client/src/schema";

type Detail = components["schemas"]["WorkspaceDetail"];
const tenantId = "00000000-0000-4000-8000-000000000001";
const sessionId = "00000000-0000-4000-8000-000000000002";
const date = "2026-10-27T11:00:00Z";

// UI-only fixtures: no account, provider, model or real candidate is modified.
async function openDetail(page: Page, overrides: Partial<Detail> = {}) {
  const detail: Detail = {
    id: sessionId, application_id: "application-ui", name: "Persona de prueba",
    phone: "+34000000000", offer_key: "cook", offer_title: "Cocina · Valencia",
    status: "handed_off", channel: "web", received_at: date, started_at: date,
    last_interaction_at: date, handed_off_at: date, owner: "Selección",
    attention: ["human"], next_step: "Atender", last_message: "Quiero hablar con una persona.",
    is_test: true, is_simulation: false, fields: {}, field_definitions: [],
    summary: "Pendiente de completar la entrevista.", provisional: true,
    snapshot_at: date, snapshot_stale: false, snapshot_status: "completed",
    fit: { score: null, coverage: 0, requirements: "pending", items: [] },
    handoff_reason: "Ha solicitado hablar con una persona.", decisions: [],
    version: "1", offer_description: "Oferta para cocina.",
    messages: Array.from({ length: 30 }, (_, index) => ({
      id: `message-${index}`, direction: index % 2 ? "in" : "out",
      text: `Mensaje ${index}: experiencia profesional y disponibilidad para la oferta.`,
      channel: "web", created_at: date, delivery_status: "",
    })),
    ...overrides,
  };
  const writes: string[] = [];
  await page.route("**/api/v1/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== "GET") {
      writes.push(path);
      await route.fulfill({ status: 400, json: { detail: "No se permiten escrituras en esta prueba." } });
      return;
    }
    if (path === "/api/v1/auth/csrf") {
      await route.fulfill({ json: { csrfToken: "synthetic-ui-csrf" } });
    } else if (path === "/api/v1/me") {
      await route.fulfill({ json: { id: "recruiter-ui", email: "recruiter@example.test", full_name: "Selección", memberships: [] } });
    } else if (path === "/api/v1/tenants") {
      await route.fulfill({ json: [{ id: tenantId, slug: "ui", name: "Cliente de prueba", role: "recruiter", active_version: "1", draft_rev: 1 }] });
    } else if (path === `/api/v1/tenants/${tenantId}/workspace/${sessionId}`) {
      await route.fulfill({ json: detail });
    } else if (path === `/api/v1/tenants/${tenantId}/sessions/${sessionId}/human`) {
      await route.fulfill({ json: { status: detail.status, owner_id: "recruiter-ui", owned_by_me: true } });
    } else if (path === `/api/v1/tenants/${tenantId}/candidates/${sessionId}`) {
      await route.fulfill({ json: { messages: [] } });
    } else {
      await route.fulfill({ status: 404, json: { detail: "Ruta fuera de esta prueba." } });
    }
  });
  await page.goto(`/t/${tenantId}/candidaturas/${sessionId}`);
  await expect(page.getByRole("heading", { name: detail.name, exact: true })).toBeVisible();
  return { detail, writes };
}

test("ficha: una tarea visible, pestañas por teclado y derivación siempre visible", async ({ page }) => {
  const { writes } = await openDetail(page);
  const conversation = page.getByRole("tab", { name: /Conversación/ });
  await expect(conversation).toHaveAttribute("aria-selected", "true");
  await conversation.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Perfil", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tabpanel")).toHaveCount(1);
  await expect(page.getByText("Ha solicitado hablar con una persona.", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Llamar al +34000000000" })).toHaveAttribute("href", "tel:+34000000000");
  await expect(page.getByRole("link", { name: "Abrir diagnóstico técnico" })).toHaveCount(0);
  await page.getByRole("button", { name: "Atender conversación", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Mensaje de HR" })).toBeFocused();
  expect(writes).toEqual([]);
});

test("ficha: consulta de entrevista fallida se distingue de no tener entrevista", async ({ page }) => {
  await openDetail(page);
  await expect(page.getByRole("alert").filter({ hasText: "No se ha podido consultar la entrevista." })).toBeVisible();
  await page.route(`**/sessions/${sessionId}/interview`, route => route.fulfill({ json: { interview: null } }));
  await page.getByRole("button", { name: "Reintentar entrevista", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "No se ha podido consultar la entrevista." })).toHaveCount(0);
});

test("ficha: conserva borrador y lectura entre pestañas, sin enviar", async ({ page }) => {
  const { detail, writes } = await openDetail(page);
  const reply = page.getByRole("textbox", { name: "Mensaje de HR" });
  await reply.fill("Respuesta todavía sin enviar.");
  const log = page.getByRole("log", { name: "Historial de conversación" });
  await log.press("Home");
  await expect.poll(() => log.evaluate(el => el.scrollTop)).toBe(0);
  await page.getByRole("tab", { name: "Perfil", exact: true }).click();
  // A background completion must not redirect the tab the recruiter is reading.
  detail.status = "completed";
  await page.waitForResponse(r => r.url().endsWith(`/workspace/${sessionId}`));
  await expect(page.getByRole("tab", { name: "Perfil", exact: true })).toHaveAttribute("aria-selected", "true");
  detail.status = "handed_off";
  await page.waitForResponse(r => r.url().endsWith(`/sessions/${sessionId}/human`));
  await page.getByRole("tab", { name: /Conversación/ }).click();
  await expect(reply).toHaveValue("Respuesta todavía sin enviar.");
  await expect.poll(() => log.evaluate(el => el.scrollTop)).toBe(0);
  expect(writes).toEqual([]);
});

test("ficha completada: evaluación inicial y motivo conservado sin confirmar", async ({ page }) => {
  const { writes } = await openDetail(page, {
    status: "completed", provisional: false,
    decisions: [{ id: "decision-ui", status: "proposed", decision_type: "qualify", reason: "Propuesta pendiente de revisión." }],
  });
  await expect(page.getByRole("tab", { name: /Evaluación/ })).toHaveAttribute("aria-selected", "true");
  const reason = page.getByRole("textbox", { name: "Motivo de la decisión" });
  await reason.fill("Motivo provisional.");
  await page.getByRole("tab", { name: /Conversación/ }).click();
  await expect(page.getByText("Conversación cerrada.")).toBeVisible();
  await page.getByRole("button", { name: "Revisar decisión", exact: true }).click();
  await expect(page.getByRole("tab", { name: /Evaluación/ })).toBeFocused();
  await expect(reason).toHaveValue("Motivo provisional.");
  expect(writes).toEqual([]);
});

test("ficha responsive: no desborda ni recorta las acciones de respuesta", async ({ page }) => {
  const { writes } = await openDetail(page);
  for (const viewport of [{ width: 1366, height: 768 }, { width: 390, height: 844 }, { width: 360, height: 740 }]) {
    await page.setViewportSize(viewport);
    await page.getByRole("button", { name: "Atender conversación", exact: true }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const panel = await page.locator(".candidate-dialogue").boundingBox();
    const controls = await page.locator("#human-reply").boundingBox();
    expect(panel).not.toBeNull();
    expect(controls).not.toBeNull();
    expect(controls!.y + controls!.height).toBeLessThanOrEqual(panel!.y + panel!.height + 1);
    // The reply form must never consume the space reserved for reading.
    const history = await page.getByRole("log").boundingBox();
    expect(history!.height).toBeGreaterThanOrEqual(viewport.width > 760 ? 420 : 380);
    await expect(page.getByRole("button", { name: "Enviar mensaje al candidato" })).toBeVisible();
    await expect.poll(() => page.getByRole("log").evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThan(1);
    await page.getByRole("tab", { name: /Evaluación/ }).click();
    await expect(page.getByText("Sin puntuación", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  expect(writes).toEqual([]);
});

test("historial: ampliar el borrador de respuesta no reduce el área de lectura", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  const { writes } = await openDetail(page);
  const log = page.getByRole("log");
  const before = await log.boundingBox();
  const reply = page.getByRole("textbox", { name: "Mensaje de HR" });
  await reply.fill("Respuesta en preparación, sin enviar.");
  const box = await reply.boundingBox();
  // Use the real resize handle rather than replacing styles in the page.
  await page.mouse.move(box!.x + box!.width - 4, box!.y + box!.height - 4);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width - 4, box!.y + box!.height + 60);
  await page.mouse.up();
  expect((await log.boundingBox())!.height).toBeCloseTo(before!.height, 0);
  await page.getByRole("tab", { name: "Perfil", exact: true }).click();
  await page.getByRole("tab", { name: /Conversación/ }).click();
  await expect(reply).toHaveValue("Respuesta en preparación, sin enviar.");
  expect(writes).toEqual([]);
});

test("pantalla completa conserva el borrador, contiene el foco y sale con Escape", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  const { writes } = await openDetail(page);
  const reply = page.getByRole("textbox", { name: "Mensaje de HR" });
  await reply.fill("Borrador que no debe perderse al ampliar.");
  const originalUrl = page.url();
  await page.getByRole("button", { name: "Pantalla completa", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Conversación con Persona de prueba" });
  await expect(dialog).toBeVisible();
  expect(await dialog.evaluate(el => el.matches(":modal"))).toBe(true);
  const panel = await dialog.boundingBox();
  expect(panel!.width).toBe(1366);
  expect(panel!.height).toBe(768);
  await expect(reply).toHaveValue("Borrador que no debe perderse al ampliar.");
  await expect(page.getByRole("button", { name: "Salir de pantalla completa" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Pantalla completa", exact: true })).toBeFocused();
  await expect(reply).toHaveValue("Borrador que no debe perderse al ampliar.");
  expect(page.url()).toBe(originalUrl);
  expect(writes).toEqual([]);
});
