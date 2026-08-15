import { describe, expect, it } from "vitest";

import { MODEL_SELECTABLE, VERDICT_TONE } from "./FindingCard";

describe("verdict tone", () => {
  it("treats only a contradiction as a finding against the citation", () => {
    const blocking = Object.entries(VERDICT_TONE)
      .filter(([, tone]) => tone === "block")
      .map(([verdict]) => verdict);
    expect(blocking).toEqual(["CONTRADICTED"]);
  });

  it("never renders absent evidence as a warning", () => {
    expect(VERDICT_TONE.UNVERIFIABLE_FROM_AVAILABLE_EVIDENCE).toBe("neutral");
    expect(VERDICT_TONE.EVIDENCE_UNAVAILABLE).toBe("neutral");
  });

  it("does not colour a qualified result as a defect", () => {
    expect(VERDICT_TONE.PARTIALLY_SUPPORTED).toBe("neutral");
  });

  it("reserves every non-neutral tone for a verdict that earns it", () => {
    const alarming = Object.entries(VERDICT_TONE).filter(([, tone]) => tone === "warn");
    expect(alarming).toEqual([]);
  });

  it("does not blame the bibliography for a resolution the server could not make", () => {
    expect(VERDICT_TONE.SOURCE_UNRESOLVED).toBe("neutral");
    expect(VERDICT_TONE.SOURCE_IDENTITY_UNCERTAIN).toBe("neutral");
  });

  it("covers every verdict, so a new one cannot render untoned", () => {
    expect(Object.keys(VERDICT_TONE)).toHaveLength(7);
  });
});

describe("model-selectable verdicts", () => {
  it("holds exactly the four a model may choose", () => {
    expect([...MODEL_SELECTABLE].sort()).toEqual([
      "CONTRADICTED",
      "PARTIALLY_SUPPORTED",
      "SUPPORTED",
      "UNVERIFIABLE_FROM_AVAILABLE_EVIDENCE",
    ]);
  });

  it("excludes the server-decided ones", () => {
    expect(MODEL_SELECTABLE.has("SOURCE_UNRESOLVED")).toBe(false);
    expect(MODEL_SELECTABLE.has("SOURCE_IDENTITY_UNCERTAIN")).toBe(false);
    expect(MODEL_SELECTABLE.has("EVIDENCE_UNAVAILABLE")).toBe(false);
  });
});
