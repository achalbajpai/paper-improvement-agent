export class AttemptKeys {
  private held: { identity: string; key: string } | null = null;

  take(identity: string, mint: () => string = () => crypto.randomUUID()): string {
    if (this.held === null || this.held.identity !== identity) {
      this.held = { identity, key: mint() };
    }
    return this.held.key;
  }

  settle(): void {
    this.held = null;
  }
}
