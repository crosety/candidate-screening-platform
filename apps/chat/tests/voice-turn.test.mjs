import assert from "node:assert/strict";
import test from "node:test";
import { VoiceTurn } from "../src/components/voiceTurn.ts";

test("the spoken waiting notice cannot reopen the microphone", () => {
  const turn = new VoiceTurn();
  turn.begin(); turn.queueAudio();
  turn.response("Estoy revisando tu respuesta...  ");
  turn.finishAudio();
  assert.equal(turn.settle(), false);
  assert.equal(turn.phase, "thinking");
});

test("the full answer must arrive and finish playing before listening", () => {
  const turn = new VoiceTurn();
  turn.begin(); turn.queueAudio();
  turn.response("Estoy revisando tu respuesta... ¿En qué ciudad vives?");
  assert.equal(turn.settle(), false);
  turn.finishAudio();
  assert.equal(turn.settle(), true);
  assert.equal(turn.phase, "listening");
});

test("all queued audio must finish, including late chunks", () => {
  const turn = new VoiceTurn();
  turn.begin(); turn.queueAudio(); turn.queueAudio();
  turn.response("¿En qué ciudad vives?");
  turn.finishAudio(); assert.equal(turn.settle(), false);
  turn.finishAudio(); turn.queueAudio(); assert.equal(turn.settle(), false);
  turn.finishAudio(); assert.equal(turn.settle(), true);
});

test("each next turn waits for its own response", () => {
  const turn = new VoiceTurn();
  turn.begin(); turn.response("Primera respuesta"); turn.settle();
  turn.begin();
  assert.equal(turn.settle(), false);
  assert.equal(turn.phase, "thinking");
});
