import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { afterAll, beforeAll } from "vitest";
import { mockServer } from "./mocks/server";

// jsdom does not implement the native dialog methods used by the application.
if (!HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
}
if (!HTMLDialogElement.prototype.close) {
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
}

beforeAll(() => mockServer.listen({ onUnhandledRequest: "error" }));

// Unmount rendered trees between tests so assertions don't bleed across
// component tests (jsdom shares one document).
afterEach(() => {
  cleanup();
  mockServer.resetHandlers();
});
afterAll(() => mockServer.close());
