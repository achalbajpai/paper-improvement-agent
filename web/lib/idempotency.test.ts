import { describe, expect, it } from "vitest";

import { AttemptKeys } from "./idempotency";

function counter() {
  let n = 0;
  return () => `key-${(n += 1)}`;
}

describe("AttemptKeys", () => {
  it("reuses the key while an outcome is unknown", () => {
    const mint = counter();
    const keys = new AttemptKeys();

    const first = keys.take("upload", mint);
    const retry = keys.take("upload", mint);

    expect(retry).toBe(first);
  });

  it("mints a new key once the outcome is known", () => {
    const mint = counter();
    const keys = new AttemptKeys();

    const first = keys.take("upload", mint);
    keys.settle();

    expect(keys.take("upload", mint)).not.toBe(first);
  });

  it("never shares a key between two different commands", () => {
    const mint = counter();
    const keys = new AttemptKeys();

    const shorten = keys.take("shorten the introduction", mint);
    const addCites = keys.take("add citations to the introduction", mint);

    expect(addCites).not.toBe(shorten);
  });

  it("does not resurrect a settled key when the same action is repeated", () => {
    const mint = counter();
    const keys = new AttemptKeys();

    const first = keys.take("review", mint);
    keys.settle();
    const second = keys.take("review", mint);
    keys.settle();

    expect(new Set([first, second]).size).toBe(2);
  });
});
