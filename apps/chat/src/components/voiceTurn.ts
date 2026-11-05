export type VoicePhase = "connecting" | "listening" | "thinking" | "speaking";

// A filler is not a completed answer. Keep the microphone quiet until the
// actual response has arrived AND its queued audio has finished playing.
export class VoiceTurn {
  waiting = false;
  answered = false;
  playing = 0;

  begin() { this.waiting = true; this.answered = false; }
  response(text: string) {
    const answer = text.replace(/Estoy revisando tu respuesta\s*(?:\.{3}|…)?/g, "").trim();
    if (answer) this.answered = true;
  }
  queueAudio() { this.waiting = true; this.playing++; }
  finishAudio() { this.playing = Math.max(0, this.playing - 1); }
  settle(): boolean {
    if (!this.waiting || !this.answered || this.playing > 0) return false;
    this.waiting = false;
    this.answered = false;
    return true;
  }
  get phase(): VoicePhase {
    return this.playing > 0 ? "speaking" : this.waiting ? "thinking" : "listening";
  }
}
