import { describe, expect, it } from "vitest";

import { resolveTab } from "./tabs";

const TABS = ["manuscript", "review", "references", "parse"] as const;

describe("resolveTab", () => {
  it("selects the tab named in the query string", () => {
    expect(resolveTab("review", TABS, "manuscript")).toBe("review");
    expect(resolveTab("parse", TABS, "manuscript")).toBe("parse");
  });

  it("falls back when no tab is named", () => {
    expect(resolveTab(null, TABS, "manuscript")).toBe("manuscript");
    expect(resolveTab(undefined, TABS, "manuscript")).toBe("manuscript");
    expect(resolveTab("", TABS, "manuscript")).toBe("manuscript");
  });

  it("falls back rather than rendering nothing for an unknown tab", () => {
    expect(resolveTab("findings", TABS, "manuscript")).toBe("manuscript");
    expect(resolveTab("Review", TABS, "manuscript")).toBe("manuscript");
  });
});
