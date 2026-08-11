let sequence = 0;

/** UI-only correlation id; intentionally independent of Web Crypto. */
export function createUploadBatchId(now = Date.now()) {
  sequence += 1;
  return `upload-${now.toString(36)}-${sequence.toString(36)}`;
}
