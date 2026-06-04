import * as vscode from "vscode";

const SECRET_KEY = "agenthq.token";

export class Auth {
  constructor(private readonly secrets: vscode.SecretStorage) {}

  async getToken(): Promise<string | undefined> {
    return this.secrets.get(SECRET_KEY);
  }

  async setToken(token: string): Promise<void> {
    await this.secrets.store(SECRET_KEY, token);
  }

  async clearToken(): Promise<void> {
    await this.secrets.delete(SECRET_KEY);
  }

  // Prompts the user when there is no stored token; returns the token
  // (newly entered or pre-existing) or undefined if the user cancelled.
  async ensureToken(): Promise<string | undefined> {
    const existing = await this.getToken();
    if (existing) return existing;
    const entered = await vscode.window.showInputBox({
      prompt: "AgentHQ API token",
      password: true,
      ignoreFocusOut: true,
      placeHolder: "Paste your AGENTHQ_TOKEN here",
    });
    if (!entered) return undefined;
    await this.setToken(entered.trim());
    return entered.trim();
  }
}
