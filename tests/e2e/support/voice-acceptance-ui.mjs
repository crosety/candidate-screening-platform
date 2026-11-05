// The live harness and protocol regression use the same public candidate controls.
export async function startBrowserVoice(page) {
  // Reopening resets consent after the sheet renders; wait for its new config,
  // otherwise a fast runner may check the previous render just before that reset.
  const configured = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/voice/config') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Hablar aquí', exact: true }).click();
  if (!(await configured).ok()) throw new Error('No se pudo preparar la voz.');
  const sheet = page.getByRole('dialog', { name: 'Hablemos por aquí', exact: true });
  await sheet.getByRole('checkbox', { name: 'Quiero activar el micrófono y continuar por voz.', exact: true }).check();
  await sheet.getByRole('button', { name: 'Empezar a hablar', exact: true }).click();
}

export function browserVoiceExit(page) {
  return page.getByRole('dialog', { name: 'Conversación por voz', exact: true }).getByRole('button', { name: 'Volver al chat', exact: true });
}
