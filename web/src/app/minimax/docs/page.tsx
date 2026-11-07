"use client";

import { BookOpen } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { useAuthGuard } from "@/lib/use-auth-guard";

type EndpointDoc = {
  category: string;
  endpoint: string;
  method: "GET" | "POST";
  path: string;
  title: string;
  description: string;
  request?: string;
  response?: string;
  parameters?: Array<{ name: string; type: string; required?: boolean; note: string }>;
};

const CATEGORY_LABEL: Record<string, string> = {
  chat: "对话",
  image: "图像",
  video: "视频",
  files: "文件",
  system: "系统",
};

const ENDPOINTS: EndpointDoc[] = [
  {
    category: "chat",
    endpoint: "completions",
    method: "POST",
    path: "/v1/chat/completions",
    title: "对话补全",
    description: "所有模型类型都从这个口进（文本、图像、视频模型共用），OpenAI 格式，支持流式与非流式。",
    request: `{
  "model": "minimax-m3",
  "messages": [{"role": "user", "content": "你好"}],
  "stream": true
}`,
    response: `{
  "id": "chatcmpl-...",
  "object": "chat.completion.chunk",
  "choices": [{"index": 0, "delta": {"content": "你好"}}]
}`,
    parameters: [
      { name: "model", type: "string", required: true, note: "见「模型」页，列表里的每个 id 都能用" },
      { name: "messages", type: "array", required: true, note: "OpenAI 消息格式" },
      { name: "stream", type: "boolean", note: "true 为 SSE 流式" },
      { name: "reasoning_effort", type: "string", note: "思考档位，low / medium / high / extended" },
    ],
  },
  {
    category: "chat",
    endpoint: "models",
    method: "GET",
    path: "/v1/models",
    title: "模型列表",
    description: "返回网关暴露的全部模型 id。OpenAI 的 schema 没有字段能表达「这是图像模型」，客户端会把每个 id 都当对话模型，所以原则是列出来的就能用。",
    response: `{
  "object": "list",
  "data": [{"id": "minimax-m3", "object": "model"}]
}`,
  },
  {
    category: "image",
    endpoint: "generations",
    method: "POST",
    path: "/v1/images/generations",
    title: "图像生成",
    description: "图像模型（minimax-image）走这个口，OpenAI 兼容格式。",
    request: `{
  "model": "minimax-image",
  "prompt": "一只猫在弹钢琴",
  "n": 1
}`,
    response: `{
  "created": 1793000000,
  "data": [{"b64_json": "..."}]
}`,
    parameters: [
      { name: "model", type: "string", required: true, note: "minimax-image" },
      { name: "prompt", type: "string", required: true, note: "提示词" },
      { name: "n", type: "integer", note: "生成张数" },
    ],
  },
  {
    category: "video",
    endpoint: "generations",
    method: "POST",
    path: "/v1/videos/generations",
    title: "视频生成",
    description: "视频模型走这个口。minimax-h3-max 约 20 秒完成，是唯一能同步返回的。",
    request: `{
  "model": "minimax-h3-max",
  "prompt": "一只猫在弹钢琴"
}`,
    parameters: [
      { name: "model", type: "string", required: true, note: "minimax-h3 / minimax-h3-max / minimax-hailuo-2-3" },
      { name: "prompt", type: "string", required: true, note: "提示词" },
    ],
  },
  {
    category: "files",
    endpoint: "upload",
    method: "POST",
    path: "/v1/files",
    title: "文件上传",
    description: "上传文件到网关，用于后续对话里引用文件。",
    parameters: [{ name: "file", type: "multipart", required: true, note: "文件字段" }],
  },
  {
    category: "system",
    endpoint: "health",
    method: "GET",
    path: "/health",
    title: "健康检查",
    description: "返回网关与账号池的存活状态。",
    response: `{"ok": true}`,
  },
];

export default function MinimaxDocsPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const categories = Array.from(new Set(ENDPOINTS.map((item) => item.category)));

  return (
    <div className="space-y-5 pt-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-stone-900">MiniMax API 文档</h1>
        <p className="mt-1 text-sm text-stone-500">
          minimax2api 对外暴露的接口。网关地址在「连接设置」里配置；调用需要客户端密钥（见「密钥」页）。
        </p>
      </div>

      {categories.map((category) => (
        <div key={category} className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
            <BookOpen className="size-4" />
            {CATEGORY_LABEL[category] || category}
          </div>
          {ENDPOINTS.filter((item) => item.category === category).map((item) => (
            <Card key={item.endpoint} className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
              <CardContent className="space-y-3 p-5">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`rounded-md px-2 py-0.5 font-mono text-xs ${
                      item.method === "GET"
                        ? "bg-emerald-50 text-emerald-700"
                        : "bg-indigo-50 text-indigo-700"
                    }`}
                  >
                    {item.method}
                  </span>
                  <code className="font-mono text-sm text-stone-800">{item.path}</code>
                </div>
                <div className="text-sm font-medium text-stone-800">{item.title}</div>
                <p className="text-sm text-stone-500">{item.description}</p>
                {item.request ? (
                  <pre className="overflow-x-auto rounded-xl bg-stone-900/95 p-3 text-xs leading-relaxed text-stone-100">
                    {item.request}
                  </pre>
                ) : null}
                {item.response ? (
                  <pre className="overflow-x-auto rounded-xl bg-stone-900/95 p-3 text-xs leading-relaxed text-stone-100">
                    {item.response}
                  </pre>
                ) : null}
                {item.parameters && item.parameters.length > 0 ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead className="text-xs text-stone-400">
                        <tr>
                          <th className="py-2 pr-4 font-normal">参数</th>
                          <th className="py-2 pr-4 font-normal">类型</th>
                          <th className="py-2 pr-4 font-normal">必填</th>
                          <th className="py-2 font-normal">说明</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-stone-100">
                        {item.parameters.map((param) => (
                          <tr key={param.name} className="text-stone-600">
                            <td className="py-2 pr-4 font-mono text-xs">{param.name}</td>
                            <td className="py-2 pr-4 text-xs">{param.type}</td>
                            <td className="py-2 pr-4 text-xs">{param.required ? "是" : "否"}</td>
                            <td className="py-2 text-xs">{param.note}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          ))}
        </div>
      ))}
    </div>
  );
}
