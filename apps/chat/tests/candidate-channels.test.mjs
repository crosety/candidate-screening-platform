import assert from "node:assert/strict";
import test from "node:test";
import { dictionaries } from "../src/i18n.ts";
import { channelMessage as localizedMessage, normalizeMobile, validMobile } from "../src/components/candidateChannels.ts";

const channelMessage = (detail, fallback, messages = dictionaries.es) => localizedMessage(detail, () => fallback)(messages);

test("mobile form accepts readable spacing but sends a normalized destination", () => {
  for (const phone of ["+34 600 000 000", "+34 (600) 000-000", "+34600000000"]) {
    assert.equal(normalizeMobile(phone), "+34600000000");
    assert.equal(validMobile(phone), true);
  }
});

test("unsupported or ambiguous destinations remain blocked", () => {
  for (const phone of ["112", "600000000", "+34900000000", "+34790000000", "+34600000000<script>", ""]) assert.equal(validMobile(phone), false);
});

test("candidate channel errors retain useful instructions without rehearsal or infrastructure details", () => {
  const fallback = "Puedes continuar por aquí.";
  for (const detail of ["Ventana de prueba cerrada", "Twilio no disponible", "Simulación detenida", "Respuestas ficticias", "Túnel cerrado", { detail: "private" }]) assert.equal(channelMessage(detail, fallback), fallback);
  assert.equal(channelMessage("Debes autorizar la llamada", fallback), "Debes autorizar la llamada");
  assert.equal(channelMessage("Comprueba tu móvil antes de volver a intentarlo", fallback), "Comprueba tu móvil antes de volver a intentarlo");
});

test("an English interface replaces Spanish server details with its own fallback", () => {
  const fallback = "You can carry on here.";
  assert.equal(channelMessage("Debes autorizar la llamada", fallback, dictionaries.en), fallback);
  assert.equal(channelMessage("Twilio no disponible", fallback, dictionaries.en), fallback);
});

test("a rejected mobile format has a useful explanation in either language", () => {
  const detail = "Indica un móvil español válido con prefijo +34.";
  assert.equal(channelMessage(detail, "unavailable", dictionaries.en), "Enter a valid Spanish mobile number with the +34 prefix.");
  assert.equal(channelMessage(detail, "unavailable", dictionaries.es), "Indica un móvil español válido con prefijo +34.");
});

test("capacity limits use the unavailable message without advertising quotas", () => {
  for (const messages of [dictionaries.es, dictionaries.en]) {
    const fallback = messages.telephone.unavailable;
    for (const detail of ["Se ha agotado el cupo autorizado de llamadas", "Se ha alcanzado el límite autorizado de dos intentos", "Presupuesto SMS agotado", "Daily call limit exceeded", "Quota exceeded"]) {
      assert.equal(channelMessage(detail, fallback, messages), fallback);
    }
  }
});
