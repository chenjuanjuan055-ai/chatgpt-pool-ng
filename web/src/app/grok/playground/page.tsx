"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowUp, Clock3, Image as ImageIcon, LoaderCircle, MessageSquare, Mic, Music, Plus,
  Search, Sparkles, Square, Trash2, Video, Wand2, X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { fetchGrokModels, uploadGrokMedia } from "@/lib/api";
import { getStoredAuthKey } from "@/store/auth";
import webConfig from "@/constants/common-env";
import { useAuthGuard } from "@/lib/use-auth-guard";

type Mode = "chat" | "image" | "video" | "voice";
type ChatMessage = { id: string; role: "user" | "assistant"; content: string; reasoning?: string };

const ASPECT_RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"];
const IMAGE_RESOLUTIONS = ["1k", "2k"];
const VIDEO_RESOLUTIONS = ["480p", "720p", "1080p"];
const VIDEO_DURATIONS = ["6", "10", "15"];
const EXTEND_DURATIONS = ["2", "4", "6", "8", "10"];
const REASONING_EFFORTS = ["auto", "none", "low", "medium", "high", "xhigh"];

const MODES: { id: Mode; label: string; icon: typeof MessageSquare }[] = [
  { id: "chat", label: "对话", icon: MessageSquare },
  { id: "image", label: "图片", icon: ImageIcon },
  { id: "video", label: "视频", icon: Video },
  { id: "voice", label: "语音", icon: Mic },
];

function uid() {
  return Math.random().toString(36).slice(2, 10);
}


function mediaUrl(url?: string) {
  if (!url) return "";
  const base = webConfig.apiUrl.replace(/\/$/, "");
  const match = url.match(/\/v1\/media\/(.+)$/);
  if (match) return `${base}/api/grok/v1/media/${match[1]}`;
  return url;
}


async function toObjectUrl(url: string) {
  const base = webConfig.apiUrl.replace(/\/$/, "");
  if (!url.startsWith(base + "/api/grok/v1/media/")) return url;
  const response = await apiFetch(url.slice(base.length));
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

async function apiFetch(path: string, init: RequestInit = {}) {
  const key = await getStoredAuthKey();
  const headers = new Headers(init.headers || {});
  headers.set("Authorization", `Bearer ${key}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const base = webConfig.apiUrl.replace(/\/$/, "");
  const response = await fetch(base + path, { ...init, headers });
  if (!response.ok) {
    const text = await response.text();
    let message = text.slice(0, 200);
    try {
      const parsed = JSON.parse(text);
      message = parsed?.detail?.error || parsed?.error?.message || parsed?.detail || message;
    } catch {
      /* 保留原始文本 */
    }
    throw new Error(typeof message === "string" ? message : "请求失败");
  }
  return response;
}

async function callV1<T>(path: string, body?: Record<string, unknown>, method = "POST"): Promise<T> {
  const response = await apiFetch(`/api/grok/v1/${path}`, {
    method,
    body: body ? JSON.stringify(body) : undefined,
  });
  return (await response.json()) as T;
}

export default function GrokPlaygroundPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [mode, setMode] = useState<Mode>("chat");
  const [models, setModels] = useState<{ id: string; capability?: string }[]>([]);
  const [model, setModel] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // chat
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [webSearch, setWebSearch] = useState(false);
  const [xSearch, setXSearch] = useState(false);
  const [reasoning, setReasoning] = useState("auto");
  const abortRef = useRef<AbortController | null>(null);

  // image
  const [imagePrompt, setImagePrompt] = useState("");
  const [imageCount, setImageCount] = useState("1");
  const [imageAspect, setImageAspect] = useState("1:1");
  const [imageResolution, setImageResolution] = useState("1k");
  const [imageQuality, setImageQuality] = useState("medium");
  const [images, setImages] = useState<string[]>([]);

  // video
  const [videoAction, setVideoAction] = useState<"generate" | "edit" | "extend">("generate");
  const [videoPrompt, setVideoPrompt] = useState("");
  const [videoAspect, setVideoAspect] = useState("16:9");
  const [videoResolution, setVideoResolution] = useState("720p");
  const [videoDuration, setVideoDuration] = useState("6");
  const [extendDuration, setExtendDuration] = useState("4");
  const [sourceVideo, setSourceVideo] = useState("");
  const [firstFrameId, setFirstFrameId] = useState("");
  const [referenceId, setReferenceId] = useState("");
  const [sourceVideoId, setSourceVideoId] = useState("");
  const [referenceVoice, setReferenceVoice] = useState("");
  const firstFrameRef = useRef<HTMLInputElement | null>(null);
  const referenceRef = useRef<HTMLInputElement | null>(null);
  const sourceVideoRef = useRef<HTMLInputElement | null>(null);
  const [videoStatus, setVideoStatus] = useState<{ status?: string; progress?: number; url?: string }>({});
  const pollRef = useRef<number | null>(null);

  // voice
  const [ttsText, setTtsText] = useState("");
  const [voiceId, setVoiceId] = useState("alloy");
  const [voiceLang, setVoiceLang] = useState("");
  const [voiceSpeed, setVoiceSpeed] = useState("1");
  const [voices, setVoices] = useState<{ voice_id: string; name?: string; language?: string }[]>([]);
  const [audioUrl, setAudioUrl] = useState("");
  const [sttText, setSttText] = useState("");
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (isCheckingAuth) return;
    void (async () => {
      try {
        const data = await fetchGrokModels();
        const list = Array.isArray(data) ? data : data?.items || [];
        setModels(list.map((item) => ({ id: item.id, capability: (item as { capability?: string }).capability })));
        if (list.length) setModel((prev) => prev || list[0].id);
      } catch (err) {
        setError(err instanceof Error ? err.message : "读取模型失败");
      }
      try {
        const data = await callV1<{ voices?: { voice_id: string; name?: string; language?: string }[] }>(
          "tts/voices",
          undefined,
          "GET",
        );
        const list = data.voices || [];
        setVoices(list);
        if (list.length) setVoiceId((prev) => (list.some((v) => v.voice_id === prev) ? prev : list[0].voice_id));
      } catch {
        /* 音色列表拿不到就用手填 */
      }
    })();
  }, [isCheckingAuth]);

  useEffect(() => () => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    abortRef.current?.abort();
  }, []);

  const modelsFor = useCallback(
    (target: Mode) => {
      const filtered = models.filter((m) => {
        const id = m.id.toLowerCase();
        if (target === "image") return id.includes("imagine") || id.includes("image");
        if (target === "video") return id.includes("video");
        if (target === "voice") return id.includes("voice") || id.includes("tts");
        return !id.includes("imagine") && !id.includes("video") && !id.includes("voice");
      });
      return filtered.length ? filtered : models;
    },
    [models],
  );

  const chatModels = modelsFor("chat");
  const imageModels = modelsFor("image");
  const videoModels = modelsFor("video");

  async function sendChat() {
    const text = prompt.trim();
    if (!text || streaming) return;
    const userMessage: ChatMessage = { id: uid(), role: "user", content: text };
    const assistantId = uid();
    const history = [...messages, userMessage];
    setMessages([...history, { id: assistantId, role: "assistant", content: "" }]);
    setPrompt("");
    setStreaming(true);
    setError("");
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const response = await apiFetch("/api/grok/v1/responses", {
        method: "POST",
        headers: { Accept: "text/event-stream" },
        signal: controller.signal,
        body: JSON.stringify({
          model,
          input: history.map((m) => ({ role: m.role, content: m.content })),
          stream: true,
          store: false,
          ...(reasoning === "auto"
            ? { reasoning: { summary: "auto" } }
            : reasoning === "none"
              ? { reasoning: { effort: "none" } }
              : { reasoning: { effort: reasoning, summary: "auto" } }),
          ...(webSearch || xSearch
            ? {
                tools: [
                  ...(webSearch ? [{ type: "web_search" }] : []),
                  ...(xSearch ? [{ type: "x_search" }] : []),
                ],
              }
            : {}),
        }),
      });
      if (!response.body) throw new Error("流式响应为空");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let content = "";
      let think = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          const raw = line.slice(5).trim();
          if (!raw || raw === "[DONE]") continue;
          let payload: Record<string, unknown>;
          try {
            payload = JSON.parse(raw);
          } catch {
            continue;
          }
          const type = String(payload.type || "");
          if (type.includes("reasoning") && typeof payload.delta === "string") think += payload.delta;
          else if (type === "response.output_text.delta" && typeof payload.delta === "string") content += payload.delta;
          else if (type === "response.output_text.done" && typeof payload.text === "string") content = payload.text;
        }
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, content, reasoning: think } : m)),
        );
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setError(err instanceof Error ? err.message : "对话失败");
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  function stopChat() {
    abortRef.current?.abort();
    setStreaming(false);
  }

  async function uploadInput(kind: "first" | "ref" | "video", file: File) {
    setBusy(true);
    setError("");
    try {
      const data = await uploadGrokMedia(file);
      const id = data.fileId || data.file_id || "";
      if (!id) throw new Error("上传没有返回 file_id");
      if (kind === "first") setFirstFrameId(id);
      else if (kind === "ref") setReferenceId(id);
      else setSourceVideoId(id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setBusy(false);
    }
  }

  async function generateImage() {
    const text = imagePrompt.trim();
    if (!text || busy) return;
    setBusy(true);
    setError("");
    setImages([]);
    try {
      const data = await callV1<{ data?: { url?: string; b64_json?: string }[] }>("images/generations", {
        model: imageModels[0]?.id || model,
        prompt: text,
        n: Number(imageCount) || 1,
        aspect_ratio: imageAspect,
        resolution: imageResolution,
        ...(imageModels[0]?.id?.endsWith("grok-imagine-image-2.0") ? { quality: imageQuality } : {}),
        response_format: "url",
        stream: false,
      });
      const raw = (data.data || []).map((item) => mediaUrl(item.url)).filter(Boolean);
      const urls = await Promise.all(raw.map((u) => toObjectUrl(u)));
      if (!urls.length) throw new Error("没有返回图片");
      setImages(urls);
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成失败");
    } finally {
      setBusy(false);
    }
  }

  function startVideoPoll(requestId: string) {
    if (pollRef.current) window.clearInterval(pollRef.current);
    setVideoStatus({ status: "pending", progress: 0 });
    pollRef.current = window.setInterval(async () => {
      try {
        const data = await callV1<{ status?: string; progress?: number; video?: { url?: string } }>(
          `videos/${encodeURIComponent(requestId)}`,
          undefined,
          "GET",
        );
        const nextUrl = data.video?.url ? await toObjectUrl(mediaUrl(data.video.url)) : "";
        setVideoStatus({ status: data.status, progress: data.progress, url: nextUrl || undefined });
        if (data.status && ["completed", "failed", "succeeded"].includes(data.status)) {
          if (pollRef.current) window.clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } catch {
        /* 轮询失败继续等待 */
      }
    }, 3000);
  }

  async function runVideo() {
    if (busy) return;
    const text = videoPrompt.trim();
    if (!text && videoAction === "generate") return;
    setBusy(true);
    setError("");
    setVideoStatus({ status: "submitting" });
    try {
      let requestId = "";
      if (videoAction === "generate") {
        const data = await callV1<{ id?: string; request_id?: string }>("videos", {
          model: videoModels[0]?.id || model,
          prompt: text,
          duration: Number(videoDuration),
          aspect_ratio: videoAspect,
          resolution: videoResolution,
          ...(firstFrameId ? { image: { file_id: firstFrameId } } : {}),
          ...(referenceId ? { reference_images: [{ file_id: referenceId }] } : {}),
          ...(referenceVoice ? { reference_voice_ids: [referenceVoice] } : {}),
        });
        requestId = data.id || data.request_id || "";
      } else if (videoAction === "edit") {
        const data = await callV1<{ id?: string; request_id?: string }>("videos/edits", {
          model: videoModels[0]?.id || model,
          prompt: text,
          video: sourceVideoId ? { file_id: sourceVideoId } : { url: sourceVideo },
        });
        requestId = data.id || data.request_id || "";
      } else {
        const data = await callV1<{ id?: string; request_id?: string }>("videos/extensions", {
          model: videoModels[0]?.id || model,
          prompt: text,
          video: sourceVideoId ? { file_id: sourceVideoId } : { url: sourceVideo },
          duration: Number(extendDuration),
        });
        requestId = data.id || data.request_id || "";
      }
      if (!requestId) throw new Error("没有拿到任务 ID");
      startVideoPoll(requestId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "视频任务失败");
      setVideoStatus({ status: "failed" });
    } finally {
      setBusy(false);
    }
  }

  async function runTts() {
    const text = ttsText.trim();
    if (!text || busy) return;
    setBusy(true);
    setError("");
    setAudioUrl("");
    try {
      const response = await apiFetch("/api/grok/v1/tts", {
        method: "POST",
        body: JSON.stringify({
          model: models.find((m) => m.id.toLowerCase().includes("voice"))?.id || "grok-voice-latest",
          text,
          voice_id: voiceId || "alloy",
          language: voiceLang || "en",
          speed: Number(voiceSpeed) || 1,
        }),
      });
      const contentType = response.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        const payload = (await response.json()) as { audio?: string; content_type?: string };
        if (!payload.audio) throw new Error("TTS 没有返回音频");
        setAudioUrl(`data:${payload.content_type || "audio/mpeg"};base64,${payload.audio}`);
      } else {
        const blob = await response.blob();
        setAudioUrl(URL.createObjectURL(blob));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "合成失败");
    } finally {
      setBusy(false);
    }
  }

  async function runStt(file: File) {
    setBusy(true);
    setError("");
    setSttText("");
    try {
      const form = new FormData();
      form.append("model", models.find((m) => m.id.toLowerCase().includes("voice"))?.id || "grok-voice-latest");
      if (voiceLang) form.append("language", voiceLang);
      form.append("format", "true");
      form.append("file", file, file.name);
      const key = await getStoredAuthKey();
      const response = await fetch(`${webConfig.apiUrl.replace(/\/$/, "")}/api/grok/v1/stt`, {
        method: "POST",
        headers: { Authorization: `Bearer ${key}`, Accept: "application/json" },
        body: form,
      });
      if (!response.ok) throw new Error((await response.text()).slice(0, 160));
      const data = (await response.json()) as { text?: string };
      setSttText(data.text || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "转写失败");
    } finally {
      setBusy(false);
    }
  }

  const activeModelList = mode === "image" ? imageModels : mode === "video" ? videoModels : chatModels;

  return (
    <div className="flex h-[calc(100vh-7rem)] flex-col pt-6">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-stone-400">Grok</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-stone-900">创作控制台</h1>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-xl bg-stone-100 p-1">
            {MODES.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => { setMode(item.id); setError(""); }}
                  className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                    mode === item.id ? "bg-white text-stone-900 shadow-sm" : "text-stone-500 hover:text-stone-700"
                  }`}
                >
                  <Icon className="size-3.5" /> {item.label}
                </button>
              );
            })}
          </div>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="h-9 max-w-[240px] rounded-lg border border-stone-200 bg-white px-2 text-xs text-stone-700"
          >
            {(activeModelList.length ? activeModelList : models).map((item) => (
              <option key={item.id} value={item.id}>{item.id}</option>
            ))}
          </select>
          {mode === "chat" ? (
            <Button variant="outline" onClick={() => setMessages([])} disabled={streaming}>
              <Plus className="mr-1 size-4" /> 新对话
            </Button>
          ) : null}
        </div>
      </div>

      {error ? (
        <div className="mb-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">{error}</div>
      ) : null}

      <Card className="flex min-h-0 flex-1 flex-col rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="flex min-h-0 flex-1 flex-col p-0">
          {mode === "chat" ? (
            <>
              <div className="min-h-0 flex-1 space-y-4 overflow-auto p-5">
                {messages.length === 0 ? (
                  <div className="flex h-full flex-col items-center justify-center text-center text-sm text-stone-400">
                    <Sparkles className="mb-2 size-6 text-stone-300" />
                    问点什么，支持联网与 X 搜索
                  </div>
                ) : (
                  messages.map((item) => (
                    <div key={item.id} className={`flex ${item.role === "user" ? "justify-end" : "justify-start"}`}>
                      <div
                        className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                          item.role === "user" ? "bg-stone-900 text-white" : "bg-stone-100 text-stone-800"
                        }`}
                      >
                        {item.reasoning ? (
                          <details className="mb-2 rounded-lg bg-white/60 px-2 py-1 text-[11px] text-stone-500">
                            <summary className="cursor-pointer">思考过程</summary>
                            <div className="mt-1 whitespace-pre-wrap">{item.reasoning}</div>
                          </details>
                        ) : null}
                        {item.content || (streaming ? "…" : "")}
                      </div>
                    </div>
                  ))
                )}
              </div>
              <div className="border-t border-stone-100 p-3">
                <div className="flex items-end gap-2">
                  <Textarea
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        void sendChat();
                      }
                    }}
                    rows={2}
                    placeholder="输入消息，Enter 发送，Shift+Enter 换行"
                    className="min-h-[44px] flex-1 resize-none"
                  />
                  {streaming ? (
                    <Button variant="outline" onClick={stopChat} className="h-10">
                      <Square className="mr-1 size-4" /> 停止
                    </Button>
                  ) : (
                    <Button onClick={() => void sendChat()} disabled={!prompt.trim()} className="h-10">
                      <ArrowUp className="mr-1 size-4" /> 发送
                    </Button>
                  )}
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-stone-500">
                  <label className="flex items-center gap-1">
                    <input type="checkbox" checked={webSearch} onChange={(e) => setWebSearch(e.target.checked)} className="size-3.5" />
                    <Search className="size-3.5" /> 联网
                  </label>
                  <label className="flex items-center gap-1">
                    <input type="checkbox" checked={xSearch} onChange={(e) => setXSearch(e.target.checked)} className="size-3.5" />
                    X 搜索
                  </label>
                  <label className="flex items-center gap-1">
                    推理
                    <select
                      value={reasoning}
                      onChange={(e) => setReasoning(e.target.value)}
                      className="rounded-md border border-stone-200 bg-white px-1 py-0.5 text-xs"
                    >
                      {REASONING_EFFORTS.map((item) => (
                        <option key={item} value={item}>{item}</option>
                      ))}
                    </select>
                  </label>
                </div>
              </div>
            </>
          ) : null}

          {mode === "image" ? (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="min-h-0 flex-1 overflow-auto p-5">
                {images.length === 0 ? (
                  <div className="flex h-full flex-col items-center justify-center text-sm text-stone-400">
                    <ImageIcon className="mb-2 size-6 text-stone-300" />
                    {busy ? "生成中…" : "描述你想要的画面"}
                  </div>
                ) : (
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                    {images.map((url) => (
                      <a key={url} href={url} target="_blank" rel="noreferrer" className="group overflow-hidden rounded-xl border border-stone-200">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={url} alt="生成结果" className="aspect-square w-full object-cover transition group-hover:scale-[1.02]" />
                      </a>
                    ))}
                  </div>
                )}
              </div>
              <div className="border-t border-stone-100 p-3">
                <div className="flex items-end gap-2">
                  <Textarea
                    value={imagePrompt}
                    onChange={(e) => setImagePrompt(e.target.value)}
                    rows={2}
                    placeholder="描述画面，例如：雨夜东京街头，霓虹灯反射在湿漉漉的路面"
                    className="flex-1 resize-none"
                  />
                  <Button onClick={() => void generateImage()} disabled={busy || !imagePrompt.trim()} className="h-10">
                    {busy ? <LoaderCircle className="mr-1 size-4 animate-spin" /> : <Wand2 className="mr-1 size-4" />}
                    生成
                  </Button>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-stone-500">
                  <label className="flex items-center gap-1">
                    比例
                    <select value={imageAspect} onChange={(e) => setImageAspect(e.target.value)} className="rounded-md border border-stone-200 bg-white px-1 py-0.5">
                      {ASPECT_RATIOS.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </label>
                  <label className="flex items-center gap-1">
                    分辨率
                    <select value={imageResolution} onChange={(e) => setImageResolution(e.target.value)} className="rounded-md border border-stone-200 bg-white px-1 py-0.5">
                      {IMAGE_RESOLUTIONS.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </label>
                  {imageModels[0]?.id?.endsWith("grok-imagine-image-2.0") ? (
                    <label className="flex items-center gap-1">
                      质量
                      <select value={imageQuality} onChange={(e) => setImageQuality(e.target.value)} className="rounded-md border border-stone-200 bg-white px-1 py-0.5">
                        <option value="low">low</option>
                        <option value="medium">medium</option>
                      </select>
                    </label>
                  ) : null}
                  <label className="flex items-center gap-1">
                    数量
                    <select value={imageCount} onChange={(e) => setImageCount(e.target.value)} className="rounded-md border border-stone-200 bg-white px-1 py-0.5">
                      {["1", "2", "3", "4"].map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                  </label>
                </div>
              </div>
            </div>
          ) : null}

          {mode === "video" ? (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="flex items-center gap-2 border-b border-stone-100 px-4 py-2 text-xs">
                {(["generate", "edit", "extend"] as const).map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => { setVideoAction(item); setError(""); }}
                    className={`rounded-lg px-3 py-1.5 transition ${
                      videoAction === item ? "bg-stone-900 text-white" : "text-stone-500 hover:bg-stone-100"
                    }`}
                  >
                    {item === "generate" ? "生成" : item === "edit" ? "编辑" : "续写"}
                  </button>
                ))}
              </div>
              <div className="min-h-0 flex-1 overflow-auto p-5">
                {videoStatus.url ? (
                  <video src={videoStatus.url} controls className="mx-auto max-h-full rounded-xl" />
                ) : videoStatus.status && videoStatus.status !== "idle" ? (
                  <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-stone-500">
                    <LoaderCircle className="size-5 animate-spin text-stone-400" />
                    {videoStatus.status} {typeof videoStatus.progress === "number" ? `· ${videoStatus.progress}%` : ""}
                    {videoStatus.progress !== undefined ? (
                      <div className="h-1.5 w-56 overflow-hidden rounded-full bg-stone-100">
                        <div className="h-full bg-stone-900 transition-all" style={{ width: `${videoStatus.progress || 0}%` }} />
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <div className="flex h-full flex-col items-center justify-center text-sm text-stone-400">
                    <Video className="mb-2 size-6 text-stone-300" />
                    描述镜头、主体和运动
                  </div>
                )}
              </div>
              <div className="border-t border-stone-100 p-3">
                <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-stone-500">
                  {videoAction === "generate" ? (
                    <>
                      <input ref={firstFrameRef} type="file" accept="image/*" className="hidden"
                             onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadInput("first", f); }} />
                      <input ref={referenceRef} type="file" accept="image/*" className="hidden"
                             onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadInput("ref", f); }} />
                      <button type="button" onClick={() => firstFrameRef.current?.click()}
                              className={`rounded-lg px-2 py-1 ${firstFrameId ? "bg-emerald-50 text-emerald-700" : "bg-stone-100"}`}>
                        {firstFrameId ? "首帧图 ✓" : "首帧图"}
                      </button>
                      <button type="button" onClick={() => referenceRef.current?.click()}
                              className={`rounded-lg px-2 py-1 ${referenceId ? "bg-emerald-50 text-emerald-700" : "bg-stone-100"}`}>
                        {referenceId ? "参考图 ✓" : "参考图"}
                      </button>
                      {firstFrameId ? (
                        <button type="button" onClick={() => setFirstFrameId("")} className="text-stone-400"><X className="size-3" /></button>
                      ) : null}
                      {referenceId ? (
                        <button type="button" onClick={() => setReferenceId("")} className="text-stone-400"><X className="size-3" /></button>
                      ) : null}
                    </>
                  ) : (
                    <>
                      <input ref={sourceVideoRef} type="file" accept="video/*" className="hidden"
                             onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadInput("video", f); }} />
                      <button type="button" onClick={() => sourceVideoRef.current?.click()}
                              className={`rounded-lg px-2 py-1 ${sourceVideoId ? "bg-emerald-50 text-emerald-700" : "bg-stone-100"}`}>
                        {sourceVideoId ? "源视频 ✓" : "上传源视频"}
                      </button>
                      {sourceVideoId ? (
                        <button type="button" onClick={() => setSourceVideoId("")} className="text-stone-400"><X className="size-3" /></button>
                      ) : null}
                      <Input
                        value={sourceVideo}
                        onChange={(e) => setSourceVideo(e.target.value)}
                        placeholder="或填源视频 URL"
                        className="h-7 max-w-[220px] text-xs"
                      />
                    </>
                  )}
                </div>
                <div className="flex items-end gap-2">
                  <Textarea
                    value={videoPrompt}
                    onChange={(e) => setVideoPrompt(e.target.value)}
                    rows={2}
                    placeholder={videoAction === "edit" ? "想怎么改这个视频" : videoAction === "extend" ? "续写方向（可选）" : "例如：镜头缓慢推进，咖啡馆窗外的雨滴落下"}
                    className="flex-1 resize-none"
                  />
                  <Button onClick={() => void runVideo()} disabled={busy} className="h-10">
                    {busy ? <LoaderCircle className="mr-1 size-4 animate-spin" /> : <Clock3 className="mr-1 size-4" />}
                    {videoAction === "generate" ? "生成" : videoAction === "edit" ? "编辑" : "续写"}
                  </Button>
                </div>
                {videoAction === "generate" ? (
                  <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-stone-500">
                    <label className="flex items-center gap-1">
                      比例
                      <select value={videoAspect} onChange={(e) => setVideoAspect(e.target.value)} className="rounded-md border border-stone-200 bg-white px-1 py-0.5">
                        {ASPECT_RATIOS.map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    </label>
                    <label className="flex items-center gap-1">
                      分辨率
                      <select value={videoResolution} onChange={(e) => setVideoResolution(e.target.value)} className="rounded-md border border-stone-200 bg-white px-1 py-0.5">
                        {VIDEO_RESOLUTIONS.map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    </label>
                    <label className="flex items-center gap-1">
                      时长
                      <select value={videoDuration} onChange={(e) => setVideoDuration(e.target.value)} className="rounded-md border border-stone-200 bg-white px-1 py-0.5">
                        {VIDEO_DURATIONS.map((d) => <option key={d} value={d}>{d}s</option>)}
                      </select>
                    </label>
                  </div>
                ) : videoAction === "extend" ? (
                  <div className="mt-2 flex items-center gap-3 text-xs text-stone-500">
                    <label className="flex items-center gap-1">
                      延长
                      <select value={extendDuration} onChange={(e) => setExtendDuration(e.target.value)} className="rounded-md border border-stone-200 bg-white px-1 py-0.5">
                        {EXTEND_DURATIONS.map((d) => <option key={d} value={d}>{d}s</option>)}
                      </select>
                    </label>
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}

          {mode === "voice" ? (
            <div className="flex min-h-0 flex-1 flex-col overflow-auto p-5">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <p className="flex items-center gap-1.5 text-sm font-medium text-stone-800">
                    <Music className="size-4 text-stone-400" /> 语音合成
                  </p>
                  <Textarea value={ttsText} onChange={(e) => setTtsText(e.target.value)} rows={4} placeholder="要朗读的文本" className="resize-none" />
                  <div className="flex flex-wrap items-center gap-2 text-xs text-stone-500">
                    <label className="flex items-center gap-1">
                      音色
                      {voices.length ? (
                        <select value={voiceId} onChange={(e) => setVoiceId(e.target.value)}
                                className="h-7 max-w-[150px] rounded-md border border-stone-200 bg-white px-1">
                          {voices.map((v) => (
                            <option key={v.voice_id} value={v.voice_id}>{v.name || v.voice_id}</option>
                          ))}
                        </select>
                      ) : (
                        <Input value={voiceId} onChange={(e) => setVoiceId(e.target.value)} className="h-7 w-24 text-xs" />
                      )}
                    </label>
                    <label className="flex items-center gap-1">
                      语言
                      <Input value={voiceLang} onChange={(e) => setVoiceLang(e.target.value)} placeholder="zh-CN" className="h-7 w-20 text-xs" />
                    </label>
                    <label className="flex items-center gap-1">
                      语速
                      <Input value={voiceSpeed} onChange={(e) => setVoiceSpeed(e.target.value)} className="h-7 w-16 text-xs" />
                    </label>
                  </div>
                  <Button onClick={() => void runTts()} disabled={busy || !ttsText.trim()}>
                    {busy ? <LoaderCircle className="mr-1 size-4 animate-spin" /> : <Mic className="mr-1 size-4" />}
                    合成
                  </Button>
                  {audioUrl ? <audio src={audioUrl} controls className="w-full" /> : null}
                </div>
                <div className="space-y-2">
                  <p className="flex items-center gap-1.5 text-sm font-medium text-stone-800">
                    <Mic className="size-4 text-stone-400" /> 语音转写
                  </p>
                  <input
                    ref={fileRef}
                    type="file"
                    accept="audio/*"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) void runStt(file);
                    }}
                  />
                  <Button variant="outline" onClick={() => fileRef.current?.click()} disabled={busy}>
                    选择音频文件
                  </Button>
                  {sttText ? (
                    <div className="rounded-xl bg-stone-50 p-3 text-xs text-stone-700">{sttText}</div>
                  ) : (
                    <p className="text-xs text-stone-400">上传音频后返回文字</p>
                  )}
                </div>
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}