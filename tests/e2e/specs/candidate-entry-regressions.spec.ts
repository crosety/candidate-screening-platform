import { expect, test } from "@playwright/test";

const chatBase = process.env.E2E_CHAT_BASE_URL ?? "http://localhost:15174";

test("SMS is only checked on request and errors stay inside its panel", async ({ page }) => {
  let statusRequests = 0;
  await page.route("**/chat/interview", route => route.fulfill({ json: { interview: null } }));
  await page.route("**/api/v1/chat/sessions", route => route.fulfill({ json: {
    session_id: "00000000-0000-4000-8000-000000000001", tenant_name: "Cliente",
    offer_title: "Oferta", status: "active", websocket_token: "test", language: "es",
  } }));
  await page.route("**/chat/telephone/status", route => route.fulfill({ json: {
    available: false, sender: "", attempt: null, reason: "",
  } }));
  await page.route("**/chat/sms-link", route => {
    expect(route.request().postDataJSON().action).toBe("status");
    statusRequests++;
    return route.fulfill({ status: 429, json: { detail: "private-rate-limit" } });
  });
  await page.routeWebSocket("**/ws/chat/**", ws => ws.send(JSON.stringify({
    type: "history", status: "active", messages: [{ id: "greeting", direction: "out",
      text: "Hola, soy el asistente virtual. ¿Cómo te llamas?", channel: "web",
      client_message_id: "", created_at: "2026-10-31T08:00:00Z" }],
  })));
  await page.goto(`${chatBase}/c/sms-panel-regression?lang=es`);
  await expect(page.getByLabel("Tu mensaje")).toBeEnabled();
  // Let effects settle: a passive visit must not poll SMS or show its error.
  await page.waitForTimeout(500);
  expect(statusRequests).toBe(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "Mi enlace por SMS", exact: true }).click();
  const panel = page.getByRole("dialog");
  await expect(panel.getByRole("alert")).toContainText("No hemos podido comprobar los SMS");
  await page.waitForTimeout(500);
  expect(statusRequests).toBe(1);
  await panel.getByRole("button", { name: "Cerrar panel", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "English", exact: true }).click();
  await expect(page.getByLabel("Your message")).toBeEnabled();
  await page.waitForTimeout(500);
  expect(statusRequests).toBe(1);
  await page.getByRole("button", { name: "Text me my link", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("We couldn't check your SMS");
  await panel.getByRole("button", { name: "Retry SMS", exact: true }).click();
  await page.waitForTimeout(500);
  expect(statusRequests).toBe(3); // reopen and one explicit retry, never a render loop
  await expect(page.getByText("private-rate-limit")).toHaveCount(0);
});

test("call status can recover explicitly without requesting a call", async ({ page }) => {
  let attempts = 0;
  await page.route("**/api/v1/chat/sessions", route => route.fulfill({ json: { session_id: "00000000-0000-4000-8000-000000000001", tenant_name: "Cliente", offer_title: "Oferta", status: "active", websocket_token: "test", language: "en" } }));
  await page.route("**/chat/interview", route => route.fulfill({ json: { interview: null } }));
  await page.route("**/chat/telephone/status", route => ++attempts <= 2
    ? route.fulfill({ status: 429, json: { detail: "private-limit" } })
    : route.fulfill({ json: { available: false, sender: "", attempt: null, reason: "" } }));
  await page.routeWebSocket("**/ws/chat/**", ws => ws.send(JSON.stringify({ type: "history", status: "active", messages: [{ id: "greeting", direction: "out", text: "Hello. What is your name?", channel: "web", created_at: "2026-10-31T08:00:00Z" }] })));
  await page.route("**/chat/telephone/request", () => { throw new Error("Must never dial in a status test"); });
  await page.goto(`${chatBase}/c/call-panel-regression?lang=en`);
  await page.getByRole("button", { name: "Call me", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toHaveCount(0);
  await expect(page.getByText("Calls aren't available right now. You can keep going in writing.", { exact: true })).toBeVisible();
});

test("the selected language reaches the server before chat opens, switches and reloads", async ({ page }) => {
  const choices: string[] = [];
  await page.route("**/api/v1/chat/sessions", route => {
    const choice = route.request().postDataJSON().language;
    choices.push(choice);
    return route.fulfill({ json: { session_id: "00000000-0000-4000-8000-000000000001", tenant_name: "Cliente", offer_title: "Oferta", status: "active", websocket_token: "test", language: choice } });
  });
  await page.route("**/chat/interview", route => route.fulfill({ json: { interview: null } }));
  await page.route("**/chat/telephone/status", route => route.fulfill({ json: { available: false, sender: "", attempt: null, reason: "" } }));
  const greeting = { id: "opening", direction: "out", text: "Hello, I am your virtual interviewer. What is your name?", channel: "web", created_at: "2026-10-31T08:00:00Z" };
  const history = [greeting];
  await page.routeWebSocket("**/ws/chat/**", ws => {
    expect(choices.at(-1)).toMatch(/^(en|es)$/);
    ws.send(JSON.stringify({ type: "history", status: "active", messages: history }));
    ws.onMessage(raw => {
      const message = JSON.parse(raw.toString());
      if (message.type !== "message") return;
      ws.send(JSON.stringify({ type: "typing", on: true }));
      // The server finishes the turn while the language switch waits on its lock.
      // The replacement socket sees history, not the old socket's typing=false.
      history.push({ ...greeting, id: "candidate", direction: "in", text: message.text });
      history.push({ ...greeting, id: "reply", text: "Thank you, Alex. What experience do you have?" });
    });
  });
  await page.goto(`${chatBase}/c/language-regression?lang=en`);
  await expect(page.getByLabel("Your message")).toBeEnabled();
  expect(choices.every(c => c === "en")).toBeTruthy();
  await page.getByLabel("Your message").fill("Alex Test");
  await page.getByLabel("Your message").press("Enter");
  await expect(page.getByLabel("Your message")).toBeDisabled();
  await page.getByRole("button", { name: "Español", exact: true }).click();
  await expect.poll(() => choices.at(-1)).toBe("es");
  await expect(page.getByLabel("Tu mensaje")).toBeEnabled();
  await expect(page.getByText("Thank you, Alex. What experience do you have?", { exact: true })).toHaveCount(1);
  await expect(page.getByText(greeting.text, { exact: true })).toHaveCount(1);
  await page.reload();
  await expect(page.getByLabel("Tu mensaje")).toBeEnabled();
  expect(choices.at(-1)).toBe("es");
  await expect(page.getByText(greeting.text, { exact: true })).toHaveCount(1);
  for (const width of [375, 1366]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.getByRole("alert")).toHaveCount(0);
    await page.screenshot({ path: test.info().outputPath(`candidate-clean-${width}.png`), fullPage: true });
  }
});

test("a background entry restores a running call when the tab becomes visible", async ({ page }) => {
  let checks = 0;
  await page.addInitScript(() => Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" }));
  await page.route("**/api/v1/chat/sessions", route => route.fulfill({ json: {
    session_id: "00000000-0000-4000-8000-000000000001", tenant_name: "Cliente",
    offer_title: "Oferta", status: "active", websocket_token: "test", language: "en",
  } }));
  await page.route("**/chat/interview", route => route.fulfill({ json: { interview: null } }));
  await page.route("**/chat/telephone/status", route => {
    checks++;
    return route.fulfill({ json: { available: true, sender: "", reason: "", attempt: { id: "call", state: "ringing" } } });
  });
  await page.route("**/chat/telephone/request", () => { throw new Error("A restored call must not be dialled again"); });
  await page.routeWebSocket("**/ws/chat/**", ws => ws.send(JSON.stringify({ type: "history", status: "active", messages: [{ id: "opening", direction: "out", text: "Hello. What is your name?", channel: "web", created_at: "2026-10-31T08:00:00Z" }] })));
  await page.goto(`${chatBase}/c/background-call?lang=en`);
  await expect(page.getByLabel("Your message")).toBeEnabled();
  expect(checks).toBe(0);
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => checks).toBeGreaterThan(0);
  await expect(page.getByLabel("Your message")).toBeDisabled();
});
