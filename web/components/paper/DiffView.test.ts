import { describe, expect, it } from "vitest";
import { align } from "./DiffView";

function highlight(before: string, after: string): { removed: string[]; added: string[] } {
  const beforeTokens = before.split(/(\s+)/);
  const afterTokens = after.split(/(\s+)/);
  const [removed, added] = align(beforeTokens, afterTokens);
  const words = (indices: Set<number>, tokens: string[]): string[] =>
    [...indices]
      .sort((a, b) => a - b)
      .map((index) => tokens[index] ?? "")
      .filter((token) => token.trim() !== "");
  return { removed: words(removed, beforeTokens), added: words(added, afterTokens) };
}

describe("align", () => {
  it("marks nothing when the text is unchanged", () => {
    const { removed, added } = highlight("The model converges.", "The model converges.");
    expect(removed).toEqual([]);
    expect(added).toEqual([]);
  });

  it("marks the cut words when a clause is removed", () => {
    const { removed, added } = highlight(
      "The model converges quickly and reliably.",
      "The model converges.",
    );
    expect(removed).toEqual(["converges", "quickly", "and", "reliably."]);
    expect(added).toEqual(["converges."]);
  });

  it("shows a substitution as both a removal and an addition", () => {
    const { removed, added } = highlight("It may reduce latency.", "It reduces latency.");
    expect(removed).toEqual(["may", "reduce"]);
    expect(added).toEqual(["reduces"]);
  });

  it("confines an inserted citation marker to the end of its sentence", () => {
    const { removed, added } = highlight(
      "Residual connections ease optimisation.",
      "Residual connections ease optimisation [12].",
    );
    expect(removed).toEqual(["optimisation."]);
    expect(added).toEqual(["optimisation", "[12]."]);
  });

  it("marks every word when a side is empty", () => {
    expect(highlight("", "Added sentence.").added).toEqual(["Added", "sentence."]);
    expect(highlight("Removed sentence.", "").removed).toEqual(["Removed", "sentence."]);
    expect(highlight("", "Added sentence.").removed).toEqual([]);
  });

  it("keeps a repeated word aligned rather than pairing the wrong occurrence", () => {
    const { removed, added } = highlight(
      "the results confirm the hypothesis",
      "the hypothesis holds",
    );
    expect(removed).toEqual(["results", "confirm", "the"]);
    expect(added).toEqual(["holds"]);
  });
});
