export type DemoCollection = {
  chunkCount: number;
  description: string;
  documentCount: number;
  id: string;
  name: string;
  updatedAt: string;
};

export type DemoDocument = {
  chunkCount: number;
  collectionId: string;
  collectionName: string;
  filename: string;
  id: string;
  imageCount: number;
  size: string;
  status: "pending" | "processing" | "ready" | "failed" | "deleting";
  updatedAt: string;
};

export const demoCollections: DemoCollection[] = [
  {
    id: "b2c1f0e8-1234-5678-9abc-def012345678",
    name: "产品知识库",
    description: "产品需求、API 规范与版本发布说明。",
    documentCount: 42,
    chunkCount: 3186,
    updatedAt: "今天 14:32",
  },
  {
    id: "9e6ad871-b748-4216-a42f-6b91785d3110",
    name: "研发文档",
    description: "架构决策、部署手册与故障排查记录。",
    documentCount: 31,
    chunkCount: 2408,
    updatedAt: "今天 11:08",
  },
  {
    id: "73e9a509-ee4f-48f8-a5b9-064a1e6fb655",
    name: "公司制度",
    description: "员工手册、行政制度与内部流程说明。",
    documentCount: 18,
    chunkCount: 1260,
    updatedAt: "昨天 16:40",
  },
  {
    id: "1f361a67-6436-4951-b5da-8c98520e052a",
    name: "客户案例",
    description: "行业解决方案、交付复盘和常见问题。",
    documentCount: 37,
    chunkCount: 1788,
    updatedAt: "7 月 29 日",
  },
];

export const demoDocuments: DemoDocument[] = [
  {
    id: "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f",
    collectionId: demoCollections[0]!.id,
    collectionName: demoCollections[0]!.name,
    filename: "API 设计规范.pdf",
    size: "2.8 MB",
    status: "ready",
    chunkCount: 186,
    imageCount: 7,
    updatedAt: "4 分钟前",
  },
  {
    id: "ef0ac733-726f-46ee-8214-00fc175fd49e",
    collectionId: demoCollections[1]!.id,
    collectionName: demoCollections[1]!.name,
    filename: "部署手册-v3.pdf",
    size: "4.1 MB",
    status: "processing",
    chunkCount: 0,
    imageCount: 0,
    updatedAt: "12 分钟前",
  },
  {
    id: "5c6f1181-4022-43fc-be08-3dd8536f39c7",
    collectionId: demoCollections[0]!.id,
    collectionName: demoCollections[0]!.name,
    filename: "Q3 产品路线图.pdf",
    size: "1.6 MB",
    status: "ready",
    chunkCount: 94,
    imageCount: 12,
    updatedAt: "今天 09:48",
  },
  {
    id: "6d136626-e13d-42dd-b8ec-abf7c4160a37",
    collectionId: demoCollections[2]!.id,
    collectionName: demoCollections[2]!.name,
    filename: "员工手册-2026.pdf",
    size: "3.3 MB",
    status: "ready",
    chunkCount: 218,
    imageCount: 3,
    updatedAt: "昨天 16:40",
  },
  {
    id: "75c4f62e-eac4-44b3-a739-7e2f5e07f91d",
    collectionId: demoCollections[3]!.id,
    collectionName: demoCollections[3]!.name,
    filename: "制造行业交付复盘.pdf",
    size: "8.7 MB",
    status: "failed",
    chunkCount: 0,
    imageCount: 0,
    updatedAt: "7 月 29 日",
  },
];
