import assert from "node:assert/strict";
import test from "node:test";
import { dictionaries, languageStorageKey, readStoredLanguage, resolveLanguage, serverText, storeLanguage, supportedLanguages } from "../src/i18n.ts";

test("an explicit ?lang= wins over everything else", () => {
  assert.equal(resolveLanguage({ search: "?lang=en", stored: "es", session: "es", preferred: ["es-ES"] }), "en");
  assert.equal(resolveLanguage({ search: "?x=1&lang=ES", stored: "en", session: "en", preferred: ["en-GB"] }), "es");
});

test("the choice remembered in this browser comes next", () => {
  assert.equal(resolveLanguage({ search: "", stored: "en", session: "es", preferred: ["es"] }), "en");
  assert.equal(resolveLanguage({ search: "?lang=fr", stored: "en", session: "es" }), "en");
});

test("then the language the server classified for the session", () => {
  assert.equal(resolveLanguage({ stored: null, session: "en", preferred: ["es-ES"] }), "en");
  assert.equal(resolveLanguage({ stored: "xx", session: "es", preferred: ["en-US"] }), "es");
});

test("then the first supported browser language, and Spanish by default", () => {
  assert.equal(resolveLanguage({ session: "", preferred: ["en-US", "es"] }), "en");
  assert.equal(resolveLanguage({ session: "fr", preferred: ["fr-FR", "es-419", "en"] }), "es");
  assert.equal(resolveLanguage({ preferred: ["de-DE", "pt-BR"] }), "es");
  assert.equal(resolveLanguage({}), "es");
});

test("stored language survives storage that throws or holds junk", () => {
  const throwing = () => { throw new Error("blocked"); };
  assert.equal(readStoredLanguage(throwing), null);
  assert.doesNotThrow(() => storeLanguage("en", throwing));
  const failingSet = () => ({ getItem: () => "es", setItem: () => { throw new Error("quota"); } });
  assert.doesNotThrow(() => storeLanguage("en", failingSet));
  const values = new Map([[languageStorageKey, "nonsense"]]);
  const memory = () => ({ getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) });
  assert.equal(readStoredLanguage(memory), null);
  storeLanguage("en", memory);
  assert.equal(readStoredLanguage(memory), "en");
});

function leaves(value, path = []) {
  if (typeof value === "function") return [[path.join("."), value("Acme")]];
  if (value && typeof value === "object") return Object.entries(value).flatMap(([key, child]) => leaves(child, [...path, key]));
  return [[path.join("."), value]];
}

test("every key exists in both languages and no text is empty", () => {
  const [reference, ...others] = supportedLanguages.map(code => new Map(leaves(dictionaries[code])));
  for (const other of others) assert.deepEqual([...other.keys()].sort(), [...reference.keys()].sort());
  for (const code of supportedLanguages) {
    for (const [key, text] of leaves(dictionaries[code])) {
      assert.equal(typeof text, "string", `${code}.${key}`);
      assert.ok(text.trim(), `${code}.${key} is empty`);
    }
    assert.equal(dictionaries[code].language, code);
  }
});

test("English copy is not a leftover Spanish string", () => {
  const spanish = new Map(leaves(dictionaries.es));
  // Language names are endonyms, identical in both dictionaries on purpose.
  const sharedOnPurpose = new Set(["switcher.names.es", "switcher.names.en"]);
  for (const [key, text] of leaves(dictionaries.en)) {
    if (sharedOnPurpose.has(key) || key === "language" || key === "locale") continue;
    if (/^[\p{Lu}\d\s·→+.,:-]{1,4}$/u.test(text)) continue;
    assert.notEqual(text, spanish.get(key), `en.${key} is still Spanish`);
    assert.doesNotMatch(text, /[áéíóúñ¿¡]/, `en.${key}`);
  }
});

test("the ordinary journey carries no rehearsal labels or providers outside the privacy detail", () => {
  for (const code of supportedLanguages) {
    for (const [key, text] of leaves(dictionaries[code])) {
      if (key === "privacy.processing") continue;
      assert.doesNotMatch(text, /\b(?:demo|prueba|test|simulaci|simulat|fictici|fictitious|OpenAI|ElevenLabs|Twilio)/i, `${code}.${key}`);
    }
  }
});

test("Spanish server text is shown verbatim only in Spanish", () => {
  const text = serverText("La conversación ya ha terminado.", m => m.connection.serverErrors.session_closed);
  assert.equal(text(dictionaries.es), "La conversación ya ha terminado.");
  assert.equal(text(dictionaries.en), dictionaries.en.connection.serverErrors.session_closed);
  assert.equal(serverText("", m => m.connection.lost)(dictionaries.es), dictionaries.es.connection.lost);
  assert.equal(serverText({ detail: "x" }, m => m.connection.lost)(dictionaries.es), dictionaries.es.connection.lost);
});
