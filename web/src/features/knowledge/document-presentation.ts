const SUPPORTED_EXTENSIONS = new Set([
  "pdf", "md", "markdown", "txt", "csv", "docx", "xlsx", "pptx",
  "html", "htm", "mhtml", "mht", "epub", "xmind",
  "png", "jpg", "jpeg", "gif", "webp", "bmp",
]);

export const DOCUMENT_ACCEPT = [
  ".pdf", ".md", ".markdown", ".txt", ".csv", ".docx", ".xlsx",
  ".pptx", ".html", ".htm", ".mhtml", ".mht", ".epub", ".xmind",
  ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
].join(",");

export const SUPPORTED_FORMAT_LABEL =
  "PDF、DOCX、XLSX、CSV、PPTX、TXT、Markdown、HTML、MHTML、EPUB、XMind 和常用图片";

export function documentExtension(filename: string) {
  const extension = filename.split(".").pop()?.toLowerCase() ?? "";
  return extension === "markdown" ? "MD" : extension.toUpperCase() || "文件";
}

export function isSupportedDocument(filename: string) {
  const extension = filename.split(".").pop()?.toLowerCase() ?? "";
  return SUPPORTED_EXTENSIONS.has(extension);
}

export function parsingMethod(filename: string) {
  const extension = filename.split(".").pop()?.toLowerCase() ?? "";
  if (["png", "jpg", "jpeg", "gif", "webp", "bmp"].includes(extension)) return "视觉解析";
  if (["csv", "xlsx"].includes(extension)) return "表格解析";
  return "标准解析";
}
