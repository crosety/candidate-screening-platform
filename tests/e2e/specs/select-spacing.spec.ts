import { expect, test } from "@playwright/test";
import { login, selectTenant } from "./helpers";

for (const width of [1440, 375]) {
  test(`los desplegables separan flecha y texto del borde a ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await login(page);
    await selectTenant(page, "Grupo Sazón");
    await page.setViewportSize({ width, height: 1000 });
    if (width < 900) await page.getByRole("button", { name: "Abrir menú", exact: true }).click();
    const client = page.getByRole("combobox", { name: "Cliente", exact: true });
    const inset = await client.evaluate(element => {
      const arrow = element.querySelector("svg:last-child")!;
      return element.getBoundingClientRect().right - arrow.getBoundingClientRect().right;
    });
    expect(inset).toBeGreaterThanOrEqual(12);
    await client.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("listbox")).toBeVisible();
    await page.keyboard.press("Escape");
    if (width < 900) await page.getByRole("button", { name: "Abrir menú", exact: true }).click();

    await page.getByRole("link", { name: "Biblioteca", exact: true }).click();
    const source = page.getByRole("combobox", { name: "Uso de Manual del repartidor", exact: true });
    await expect(source).toBeVisible();
    const controls = page.locator("select:visible");
    for (const control of await controls.all()) {
      const style = await control.evaluate(element => {
        const css = getComputedStyle(element);
        return { appearance: css.appearance, padding: parseFloat(css.paddingRight), image: css.backgroundImage, position: css.backgroundPositionX };
      });
      expect(style.appearance).toBe("none");
      expect(style.padding).toBeGreaterThanOrEqual(40);
      expect(style.image).not.toBe("none");
      expect(style.position).toMatch(/(?:100% - 14px|right 14px)/);
    }
    const original = await source.inputValue();
    await source.selectOption(original === "context" ? "search" : "context");
    await expect(source).not.toHaveValue(original);
    await source.selectOption(original);
    await source.focus();
    await expect(source).toBeFocused();
    await expect(source).toHaveCSS("outline-style", "solid");
    await page.screenshot({ path: testInfo.outputPath(`selects-${width}.png`), fullPage: true });
    await page.emulateMedia({ forcedColors: "active" });
    await expect(source).toHaveCSS("appearance", "auto");
    await expect(source).toHaveCSS("background-image", "none");
  });
}
