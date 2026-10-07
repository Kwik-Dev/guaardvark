import { describe, it, expect } from "vitest";
import { checkForWakeWord } from "../wakeWordMatcher";

describe("checkForWakeWord", () => {
  const cases = [
    // [name, transcript, detected, remainder]
    ["Guaardvark", "Hey Guaardvark, what's the weather?", true, "what's the weather?"],
    ["Guaardvark", "hey guard vark open my notes", true, "open my notes"],
    ["Guaardvark", "Hey, Guard Bark.", true, ""],
    ["Guaardvark", "OK Guardvark. Make an image of a cat.", true, "Make an image of a cat."],
    ["Guaardvark", "Guaardvark, list my projects", true, "list my projects"],
    ["Guaardvark", "Hey Gord Vark start the timer", true, "start the timer"],
    ["Guaardvark", "We should guard the backyard tonight", false, null],
    ["Guaardvark", "What is the weather tomorrow?", false, null],
    // A white-labelled install answers to its own name, not the engine's.
    ["Jarvis", "Hey Jarvis, turn on the lights", true, "turn on the lights"],
    ["Jarvis", "jarvis what time is it", true, "what time is it"],
    ["Jarvis", "Hey Guaardvark, what time is it", false, null],
    ["Jarvis", "Travis sent the report", false, null],
    ["Nova Assistant", "Hello Nova Assistant. Summarise this page.", true, "Summarise this page."],
    // Short names need a greeting and a close match.
    ["Ducky", "hey ducky read my email", true, "read my email"],
    ["Ducky", "Hey Duckie, play music", true, "play music"],
    ["Ducky", "my rubber ducky is yellow", false, null],
    ["Ducky", "hey buddy how are you", false, null],
  ];

  it.each(cases)("%s / %j", (name, transcript, detected, remainder) => {
    const result = checkForWakeWord(transcript, name);
    expect(result.detected).toBe(detected);
    if (detected) expect(result.remainder).toBe(remainder);
    else expect(result.remainder).toBe(transcript);
  });

  it("returns nothing for empty input or a missing name", () => {
    expect(checkForWakeWord("", "Guaardvark")).toEqual({ detected: false, remainder: "" });
    expect(checkForWakeWord("hey guaardvark", "")).toMatchObject({ detected: false });
  });
});
