package gateway

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode/utf8"

	"minimax2api/internal/config"
	"minimax2api/internal/minimax"
	"minimax2api/internal/pool"
	"minimax2api/internal/store"
)

// Version is reported by /health.
const Version = "0.1.0"

// Gateway exposes the OpenAI-compatible surface.
type Gateway struct {
	store    *store.Store
	pool     *pool.Pool
	client   *minimax.Client
	settings func() config.Settings
	limiter  *limiter
	// files is the local registry behind /v1/files, built on first use from
	// the media settings. See files.go.
	files     *fileStore
	filesOnce sync.Once
}

func New(st *store.Store, p *pool.Pool, client *minimax.Client, settings func() config.Settings) *Gateway {
	return &Gateway{
		store:    st,
		pool:     p,
		client:   client,
		settings: settings,
		limiter:  newLimiter(),
	}
}

// respWriter records whether the response has already been committed.
type respWriter struct {
	http.ResponseWriter
	wroteHeader bool
}

func (w *respWriter) WriteHeader(status int) {
	if w.wroteHeader {
		return
	}
	w.wroteHeader = true
	w.ResponseWriter.WriteHeader(status)
}

func (w *respWriter) Write(data []byte) (int, error) {
	if !w.wroteHeader {
		w.WriteHeader(http.StatusOK)
	}
	return w.ResponseWriter.Write(data)
}

func (w *respWriter) Flush() {
	if flusher, ok := w.ResponseWriter.(http.Flusher); ok {
		flusher.Flush()
	}
}

// ------------------------------------------------------------- chat handler

type chatRequest struct {
	Model    string        `json:"model"`
	Messages []chatMessage `json:"messages"`
	Stream   bool          `json:"stream"`
	User     string        `json:"user"`
	Tools    []toolSpec    `json:"tools"`
}

type chatMessage struct {
	Role    string          `json:"role"`
	Content json.RawMessage `json:"content"`
	// Tool history. The upstream has no field for either, so both are folded
	// into the flattened prompt in the same block format the emulation asks
	// the model to produce. Without them a tool loop restarts every round:
	// the model sees its own results but not the call that asked for them.
	ToolCalls  []toolCallWire `json:"tool_calls"`
	ToolCallID string         `json:"tool_call_id"`
	Name       string         `json:"name"`
}

type toolCallWire struct {
	ID       string `json:"id"`
	Type     string `json:"type"`
	Function struct {
		Name      string `json:"name"`
		Arguments string `json:"arguments"`
	} `json:"function"`
}

type contentPart struct {
	Type     string `json:"type"`
	Text     string `json:"text"`
	ImageURL struct {
		URL string `json:"url"`
	} `json:"image_url"`
	File struct {
		FileID   string `json:"file_id"`
		Filename string `json:"filename"`
		FileData string `json:"file_data"`
	} `json:"file"`
}

// ChatCompletions implements POST /v1/chat/completions.
func (g *Gateway) ChatCompletions(w http.ResponseWriter, r *http.Request) {
	out := &respWriter{ResponseWriter: w}
	started := time.Now()

	key, err := g.authenticate(r)
	if err != nil {
		writeError(out, http.StatusUnauthorized, err.Error())
		return
	}
	if err := g.limiter.acquire(key); err != nil {
		writeError(out, http.StatusTooManyRequests, err.Error())
		return
	}
	defer g.limiter.release(key)

	// 64MB: inline `file_data` attachments are base64 inside the JSON body, so
	// a ~40MB document costs ~54MB encoded. Bigger files belong in /v1/files.
	body, err := io.ReadAll(io.LimitReader(r.Body, 64<<20))
	if err != nil {
		writeError(out, http.StatusBadRequest, "cannot read request body")
		return
	}

	var request chatRequest
	if err := json.Unmarshal(body, &request); err != nil {
		writeError(out, http.StatusBadRequest, "invalid JSON body")
		return
	}

	modelID := request.Model
	if modelID == "" {
		modelID = "minimax-agent"
	}
	model, ok := g.store.ModelByID(modelID)
	if !ok || !model.Enabled {
		writeError(out, http.StatusBadRequest, fmt.Sprintf("unknown or disabled model %q", modelID))
		return
	}
	// Every catalogue type is reachable here, and that is the point of the
	// catalogue rather than an accident. `/v1/models` cannot say what a model
	// *is* — the schema is id/object/created/owned_by and nothing else — so a
	// chat client fills its model picker with every id it finds and sends the
	// user's message to this endpoint whichever one was picked. A model that is
	// listed here and then refused here is therefore a trap, not a boundary:
	// the caller sees a 400 and has no way to tell it apart from a broken
	// gateway. Video entries were already accepted (they are rewritten into a
	// plugin reference); image entries are dispatched to the image turn below.
	// Only a type this build does not know is rejected.
	switch model.Type {
	case store.ModelTypeChat, store.ModelTypeVideo, store.ModelTypeImage:
	default:
		writeError(out, http.StatusBadRequest, fmt.Sprintf("model %q is not a chat model", modelID))
		return
	}

	prompt, images, files, err := g.buildPrompt(r.Context(), request.Messages)
	if err != nil {
		writeError(out, http.StatusBadRequest, err.Error())
		return
	}
	// The same cap the video endpoint enforces, applied where the pictures
	// arrive as message content rather than as request fields. A chat turn has
	// nowhere to report a trim — its answer is the agent's own prose — so this
	// one happens silently; VideoGenerations reports the identical trim.
	if keep, _ := referenceLimit(model, len(images)); keep < len(images) {
		images = images[:keep]
	}
	if prompt == "" && len(images) == 0 && len(files) == 0 {
		writeError(out, http.StatusBadRequest, "messages must contain text, image or file content")
		return
	}

	if model.Type == store.ModelTypeImage {
		g.chatImage(out, r, key, model, request, prompt, images, body, started)
		return
	}

	// Every catalogue entry reaches the same upstream agent, so the model id is
	// not forwarded. It only steers local behaviour: the thinking variant asks
	// the adapter to surface the reasoning stream, and a video entry rewrites the
	// turn into a plugin reference with its generation parameters attached.
	mode := ""
	if model.ID == "minimax-m3-thinking" {
		mode = "think"
	}

	settings := g.settings()
	prompt = g.outgoingText(settings, model, prompt, minimax.VideoOptions{})

	// Tool declarations ride along in the turn text, because the upstream body
	// has no field that could carry them. tools.go records the evidence and the
	// cost of the emulation; nothing here should be read as native support.
	toolsEnabled := model.Type == store.ModelTypeChat && hasToolDeclarations(request.Tools)
	if toolsEnabled {
		prompt = injectTools(prompt, request.Tools)
	}

	splitter := newToolSplitter(toolsEnabled)
	var onDelta, onThinking func(string)
	// emitted tracks what the caller has actually been shown. It is not
	// out.wroteHeader: the role frame goes out before the upstream is called,
	// so the header says nothing about whether an answer has started.
	var emitted atomic.Bool

	var chunkMu sync.Mutex
	writeChunk := func(delta map[string]any, finish any) {
		chunkMu.Lock()
		defer chunkMu.Unlock()
		ensureStreamHeaders(out)
		writeChunkRaw(out, model, delta, finish)
		out.Flush()
	}

	// A balance refusal is short and arrives first, and by the time the turn
	// ends it has already been streamed — which would make the one situation
	// that most needs a retry the one that cannot have it. So the opening of
	// the answer is held back until it can no longer be a refusal. Past the
	// probe window, or as soon as the text stops looking like one, the buffer
	// is released in a single chunk; the delay is one sentence at most.
	const refusalProbeRunes = 48
	const refusalGiveUpRunes = 400
	var gateMu sync.Mutex
	var gate strings.Builder
	gateOpen := false

	flushGate := func() {
		gateMu.Lock()
		if gateOpen || gate.Len() == 0 {
			gateMu.Unlock()
			return
		}
		gateOpen = true
		buffered := gate.String()
		gate.Reset()
		gateMu.Unlock()
		writeChunk(map[string]any{"content": buffered}, nil)
		emitted.Store(true)
	}
	resetGate := func() {
		gateMu.Lock()
		gate.Reset()
		gateOpen = false
		gateMu.Unlock()
		splitter = newToolSplitter(toolsEnabled)
	}

	if request.Stream {
		// A tool turn is buffered instead of streamed. The model often ends a
		// turn by narrating the next step ("我先看看子目录，再写介绍") without
		// emitting the call, and that can only be repaired by replaying the
		// turn — which is impossible once the caller has read the text.
		if !toolsEnabled {
			onDelta = func(text string) {
				safe := splitter.push(text)
				if safe == "" {
					return
				}
				gateMu.Lock()
				if gateOpen {
					gateMu.Unlock()
					writeChunk(map[string]any{"content": safe}, nil)
					emitted.Store(true)
					return
				}
				gate.WriteString(safe)
				held := gate.String()
				runes := utf8.RuneCountInString(held)
				switch {
				case isCreditRefusal(held):
					// Hold it. If another account answers, resetGate drops this;
					// if every account refuses, flushGate hands it back at the end.
				case runes < refusalProbeRunes:
					// Too early to rule a refusal out.
				case runes < refusalGiveUpRunes && containsCreditTopic(held):
					// Talks about a balance but has not refused (yet): an answer
					// about credits looks like this for a few more words.
				default:
					gate.Reset()
					gateOpen = true
					gateMu.Unlock()
					writeChunk(map[string]any{"content": held}, nil)
					emitted.Store(true)
					return
				}
				gateMu.Unlock()
			}
			onThinking = func(text string) {
				// Reasoning is shown, so the caller has committed to this attempt.
				flushGate()
				writeChunk(map[string]any{"reasoning_content": text}, nil)
				emitted.Store(true)
			}
		}
		// The role frame opens the message and goes out before the upstream is
		// called.
		writeChunk(map[string]any{"role": "assistant", "content": ""}, nil)
	}

	turn, lastErr := g.runTurn(r.Context(), turnRequest{
		Prompt:     prompt,
		Images:     images,
		Files:      files,
		Mode:       mode,
		Model:      model,
		SessionKey: request.User,
		Started:    started,
		OnDelta:    onDelta,
		OnThinking: onThinking,
		Committed:  func() bool { return emitted.Load() },
		OnAttempt:  resetGate,
	})

	// A tool turn that ends by describing the next step is a dead end: the
	// caller prints the sentence and waits for a call that never comes. The
	// answer is replayed once with the narration folded back into the prompt
	// and an explicit instruction to continue. Tool turns are buffered above,
	// so the caller has not seen any of it yet.
	if lastErr == nil && toolsEnabled && turn.Result != nil {
		if narration, calls := splitToolCalls(turn.Result.Text); len(calls) == 0 && looksLikeStall(narration) {
			retryPrompt := prompt + "\n助手：" + strings.TrimSpace(narration) +
				"\n[继续] 你只描述了下一步，没有输出指令块。现在直接输出下一个 " +
				toolOpenTag + " 块，不要解释。"
			retryTurn, retryErr := g.runTurn(r.Context(), turnRequest{
				Prompt:     retryPrompt,
				Images:     images,
				Files:      files,
				Mode:       mode,
				Model:      model,
				SessionKey: request.User,
				Started:    started,
			})
			if retryErr == nil && retryTurn.Result != nil {
				if _, retryCalls := splitToolCalls(retryTurn.Result.Text); len(retryCalls) > 0 {
					turn = retryTurn
				}
			}
		}
	}

	accountName := turn.AccountName
	promptTokens := estimateTokens(prompt)
	if lastErr != nil {
		g.recordAudit(r, key, model, accountName, started, upstreamStatus(lastErr), 0, promptTokens, 0, request.Stream, turn.Retries, string(body), "", lastErr.Error())
		if out.wroteHeader {
			writeSSEError(out, lastErr.Error())
			return
		}
		writeError(out, upstreamStatus(lastErr), lastErr.Error())
		return
	}
	result := turn.Result
	if result == nil {
		writeError(out, http.StatusBadGateway, "upstream returned no result")
		return
	}

	// A call block is removed from the prose only when it parsed. A block that
	// is present but unreadable stays where it is: deleting text the caller
	// never got to see would be worse than showing it.
	text, calls := result.Text, []toolCall(nil)
	if toolsEnabled {
		text, calls = splitToolCalls(result.Text)
	}

	completionTokens := estimateTokens(result.Text + result.Thinking)
	g.store.RecordModelUsage(model.ID, 1, int64(promptTokens+completionTokens))
	g.store.BumpClientKeyUsage(key.ID)

	if request.Stream {
		// Whatever the probe held back belongs before the tail, so it leaves
		// first — including the whole answer when it never opened.
		flushGate()
		if turn.Streamed {
			// The splitter owns the tail: every byte already emitted came out of
			// it, so only it knows what is still being held back.
			tail, held := splitter.finish()
			if tail != "" {
				writeChunk(map[string]any{"content": tail}, nil)
			}
			if held != nil {
				calls = held
			}
		} else {
			if result.Thinking != "" {
				writeChunk(map[string]any{"reasoning_content": result.Thinking}, nil)
			}
			if text != "" {
				writeChunk(map[string]any{"content": text}, nil)
			}
		}
		mediaURLs := g.persistMediaList(result.Media, prompt, model.ID, accountName)
		g.finishStream(out, model, mediaURLs, calls)
		g.recordAudit(r, key, model, accountName, started, http.StatusOK, turn.FirstToken, promptTokens, completionTokens, true, turn.Retries, string(body), result.Text, "")
		return
	}

	message := map[string]any{
		"role":              "assistant",
		"content":           text,
		"reasoning_content": result.Thinking,
	}
	finishReason := "stop"
	if len(calls) > 0 {
		message["tool_calls"] = toolCallsPayload(calls)
		finishReason = "tool_calls"
	}

	response := map[string]any{
		"id":      "chatcmpl-" + randomID(12),
		"object":  "chat.completion",
		"created": time.Now().Unix(),
		"model":   model.ID,
		"choices": []any{
			map[string]any{
				"index":         0,
				"message":       message,
				"finish_reason": finishReason,
			},
		},
		"usage": map[string]any{
			"prompt_tokens":     promptTokens,
			"completion_tokens": completionTokens,
			"total_tokens":      promptTokens + completionTokens,
		},
	}
	if len(result.Media) > 0 {
		response["media"] = g.persistMediaList(result.Media, prompt, model.ID, accountName)
	}

	raw, _ := json.Marshal(response)
	g.recordAudit(r, key, model, accountName, started, http.StatusOK, turn.FirstToken, promptTokens, completionTokens, false, turn.Retries, string(body), string(raw), "")
	out.Header().Set("content-type", "application/json")
	out.WriteHeader(http.StatusOK)
	_, _ = out.Write(raw)
}

// turnRequest is one call to the upstream: what to send, which pool to draw
// from, and how deltas leave.
type turnRequest struct {
	Prompt     string
	Images     []minimax.UploadedImage
	Files      []minimax.PendingAttachment
	Mode       string
	Model      *store.ModelConfig
	SessionKey string
	Started    time.Time
	OnDelta    func(string)
	OnThinking func(string)
	// Committed reports whether the response has already begun. A retry is only
	// safe while nothing has been written: once the caller has seen a byte, a
	// second account would append a second answer to the first.
	Committed func() bool
	// OnAttempt runs before every attempt, including the first. It lets the
	// caller drop state an attempt may have buffered but never emitted.
	OnAttempt func()
}

// turnResult carries what the turn produced plus the bookkeeping an audit row
// needs.
type turnResult struct {
	Result      *minimax.Result
	AccountName string
	FirstToken  int64
	Retries     int
	Streamed    bool
}

// runTurn leases an account, calls the upstream, and retries across accounts
// while nothing has been committed to the client.
//
// Every endpoint shares this one path. It is a single function rather than two
// so that failover, cooldown accounting and token estimation cannot drift apart
// between the OpenAI and Anthropic front ends.
func (g *Gateway) runTurn(ctx context.Context, req turnRequest) (*turnResult, error) {
	settings := g.settings()
	out := &turnResult{}

	var (
		mu         sync.Mutex
		firstToken int64
		streamed   bool
	)

	markFirst := func() {
		mu.Lock()
		defer mu.Unlock()
		if firstToken == 0 {
			firstToken = time.Since(req.Started).Milliseconds()
		}
		streamed = true
	}

	var lastErr error
	var lastRefusal *minimax.Result
	for attempt := 0; attempt < settings.Routing.MaxAttempts; attempt++ {
		// A retry gets a clean slate. The attempt about to be replayed must not
		// leave its token timing — or, on the chat path, its buffered opening
		// text — behind for the account that answers next.
		mu.Lock()
		firstToken, streamed = 0, false
		mu.Unlock()
		if req.OnAttempt != nil {
			req.OnAttempt()
		}

		lease, err := g.pool.Acquire(ctx, req.SessionKey)
		if err != nil {
			// Nothing left to try — but a refusal from an earlier account is
			// still a better answer than "all accounts are busy", because it
			// says what is actually wrong.
			lastErr = err
			break
		}
		out.AccountName = displayName(lease.Account)
		if attempt > 0 {
			out.Retries = attempt
		}

		opts := minimax.Options{
			Credential:   CredentialOf(lease.Account),
			Text:         req.Prompt,
			Mode:         req.Mode,
			Images:       req.Images,
			Files:        req.Files,
			ModelPayload: modelObject(req.Model),
			Timeout:      timeoutFor(settings, req.Model),
			IdleTimeout:  settings.StreamIdleTimeout(),
		}
		if req.OnDelta != nil {
			opts.OnDelta = func(text string) {
				markFirst()
				req.OnDelta(text)
			}
		}
		if req.OnThinking != nil {
			opts.OnThinking = func(text string) {
				markFirst()
				req.OnThinking(text)
			}
		}

		result, err := g.client.Completion(ctx, opts)
		// A spent account answers rather than failing, and what it answers is a
		// refusal. Both shapes are the same fact, and both are retryable on
		// another account — unlike ErrUpstreamRejected, where the request
		// itself is the problem.
		refused := err == nil && isCreditRefusal(result.Text)
		creditError := err != nil && isCreditRefusalError(err)
		if refused || creditError {
			err = errOutOfCredit
		}
		lease.Release(err)

		lastErr = err
		if err == nil {
			mu.Lock()
			out.FirstToken, out.Streamed = firstToken, streamed
			mu.Unlock()
			if out.FirstToken == 0 {
				out.FirstToken = time.Since(req.Started).Milliseconds()
			}
			out.Result = result
			return out, nil
		}
		if refused {
			lastRefusal = result
			// Nothing has reached the caller, so the turn can still be replayed.
			// Once text has gone out, that text is the answer: a second account
			// would append a second reply to a message already being read.
			if req.Committed != nil && req.Committed() {
				out.Result = result
				mu.Lock()
				out.FirstToken, out.Streamed = firstToken, streamed
				mu.Unlock()
				return out, nil
			}
			continue
		}
		// A refusal is final for this request: the same call on the next account
		// draws the same answer, and every attempt spends an account to learn
		// nothing. See minimax.ErrUpstreamRejected.
		if ctx.Err() != nil || isPoolExhausted(err) || errors.Is(err, minimax.ErrUpstreamRejected) {
			break
		}
		if req.Committed != nil && req.Committed() {
			break
		}
	}

	mu.Lock()
	out.FirstToken, out.Streamed = firstToken, streamed
	mu.Unlock()
	// Every account refused for the same reason. The refusal text is the most
	// useful thing the pool produced, so hand it back rather than a bare error.
	if lastRefusal != nil {
		out.Result = lastRefusal
		return out, nil
	}
	return out, lastErr
}

// ensureStreamHeaders writes the SSE preamble exactly once, so every caller
// that emits a chunk gets the same headers no matter who writes first.
func ensureStreamHeaders(out *respWriter) {
	if out.wroteHeader {
		return
	}
	out.Header().Set("content-type", "text/event-stream")
	out.Header().Set("cache-control", "no-cache")
	out.Header().Set("connection", "keep-alive")
	out.Header().Set("x-accel-buffering", "no")
	out.WriteHeader(http.StatusOK)
}

// finishStream terminates the SSE response with whatever is left: media URLs
// and any tool calls the model asked for.
//
// Text that never streamed is written by the caller, not here — it needs the
// same call-block handling as the streamed path, and splitting that logic in
// two is how the two paths drift apart.
func (g *Gateway) finishStream(out *respWriter, model *store.ModelConfig, mediaURLs []string, calls []toolCall) {
	ensureStreamHeaders(out)
	if len(mediaURLs) > 0 {
		writeChunkRaw(out, model, map[string]any{"media": mediaURLs}, nil)
	}
	if len(calls) > 0 {
		writeChunkRaw(out, model, map[string]any{"tool_calls": streamedToolCallDeltas(calls)}, "tool_calls")
	} else {
		writeChunkRaw(out, model, map[string]any{}, "stop")
	}
	_, _ = io.WriteString(out, "data: [DONE]\n\n")
	out.Flush()
}

// streamedToolCallDeltas renders calls in the incremental shape OpenAI streams.
// The arguments arrive whole rather than token by token; a client concatenates
// deltas, so one complete delta is a valid sequence of one.
func streamedToolCallDeltas(calls []toolCall) []map[string]any {
	out := make([]map[string]any, 0, len(calls))
	for index, call := range calls {
		out = append(out, map[string]any{
			"index": index,
			"id":    call.ID,
			"type":  "function",
			"function": map[string]any{
				"name":      call.Name,
				"arguments": call.Arguments,
			},
		})
	}
	return out
}

func writeChunkRaw(out *respWriter, model *store.ModelConfig, delta map[string]any, finish any) {
	payload := map[string]any{
		"id":      "chatcmpl-" + randomID(12),
		"object":  "chat.completion.chunk",
		"created": time.Now().Unix(),
		"model":   model.ID,
		"choices": []any{map[string]any{"index": 0, "delta": delta, "finish_reason": finish}},
	}
	raw, _ := json.Marshal(payload)
	_, _ = fmt.Fprintf(out, "data: %s\n\n", raw)
}

// ------------------------------------------------------------ prompt build

// buildPrompt flattens the conversation into the single content string the
// agent expects, and collects the attachments it should carry: images by URL,
// files as pending uploads (bytes are resolved from /v1/files or the request
// body and pushed to the object store by the turn itself, with the sending
// account's credential).
func (g *Gateway) buildPrompt(ctx context.Context, messages []chatMessage) (string, []minimax.UploadedImage, []minimax.PendingAttachment, error) {
	if len(messages) == 0 {
		return "", nil, nil, nil
	}
	var builder strings.Builder
	var images []minimax.UploadedImage
	var files []minimax.PendingAttachment

	for _, message := range messages {
		text, urls, fileParts := parseContent(message.Content)
		switch message.Role {
		case "system":
			builder.WriteString("[系统指令] " + text + "\n\n")
		case "assistant":
			builder.WriteString("助手：" + text)
			if len(message.ToolCalls) > 0 {
				builder.WriteString("\n" + toolOpenTag)
				for _, call := range message.ToolCalls {
					name := strings.TrimSpace(call.Function.Name)
					if name == "" {
						continue
					}
					var arguments any
					if err := json.Unmarshal([]byte(call.Function.Arguments), &arguments); err != nil {
						arguments = map[string]any{}
					}
					line, _ := json.Marshal(map[string]any{"name": name, "arguments": arguments})
					builder.WriteString("\n" + string(line))
				}
				builder.WriteString("\n" + toolCloseTag)
			}
			builder.WriteString("\n")
		case "tool":
			label := strings.TrimSpace(message.Name)
			if label == "" {
				label = strings.TrimSpace(message.ToolCallID)
			}
			if label == "" {
				label = "unknown"
			}
			builder.WriteString("[工具结果 " + label + "] " + text + "\n")
		default:
			if len(messages) > 1 {
				builder.WriteString("用户：" + text + "\n")
			} else {
				builder.WriteString(text + "\n")
			}
		}

		for _, raw := range urls {
			image, err := g.resolveImage(raw)
			if err != nil {
				return "", nil, nil, err
			}
			images = append(images, image)
		}
		for _, part := range fileParts {
			file, err := g.resolveFilePart(part)
			if err != nil {
				return "", nil, nil, err
			}
			files = append(files, file)
		}
	}

	return strings.TrimSpace(builder.String()), images, files, nil
}

// resolveImage turns an OpenAI image reference into something the agent can
// fetch on its own.
//
// Remote URLs pass through untouched. Inline data URIs are written into the
// media directory and republished under Media.PublicBaseURL, because the agent
// downloads attachments server-side and cannot dereference a data: URI. When no
// public base URL is configured the request is rejected with an actionable
// message rather than being forwarded to fail opaquely upstream.
func (g *Gateway) resolveImage(raw string) (minimax.UploadedImage, error) {
	raw = strings.TrimSpace(raw)
	if !strings.HasPrefix(raw, "data:") {
		// Anything that is not a data URI has to be a URL the agent can fetch
		// itself. Without this the fallthrough is a *value* that looks fine
		// here and fails upstream naming nothing — and the value it happens to
		// is a bare base64 payload, which is what a client sends when it
		// assumes these endpoints follow the image-edit convention.
		//
		// The value is quoted to 60 characters because a base64 payload is
		// megabytes long and this string reaches the audit log.
		if !strings.HasPrefix(raw, "http://") && !strings.HasPrefix(raw, "https://") {
			return minimax.UploadedImage{}, fmt.Errorf(
				"reference image must be an http(s) URL or a data: URI — the agent downloads references server-side, so a bare base64 payload or a local path cannot work (got %.60q)",
				raw)
		}
		return minimax.UploadedImage{URL: raw, Name: imageName(raw)}, nil
	}

	index := strings.Index(raw, ",")
	if index < 0 {
		return minimax.UploadedImage{}, fmt.Errorf("invalid data URI")
	}
	mime := "image/png"
	if semi := strings.Index(raw[:index], ";"); semi > 5 {
		if colon := strings.Index(raw[:semi], ":"); colon >= 0 {
			mime = raw[colon+1 : semi]
		}
	}
	decoded, err := base64.StdEncoding.DecodeString(strings.TrimSpace(raw[index+1:]))
	if err != nil {
		return minimax.UploadedImage{}, fmt.Errorf("invalid base64 image payload: %w", err)
	}

	// Carried as bytes and pushed to the object store inside the turn. The
	// upstream takes an attachment only with an object key owned by the turn's
	// account (or a URL on its own allow-list), so writing the file somewhere
	// we host and sending that URL is answered with "attachment requires one
	// owned object_key or allowed legacy URL".
	return minimax.UploadedImage{
		Name:      "inline_" + randomID(12) + extForMime(mime),
		MimeType:  mime,
		SizeBytes: int64(len(decoded)),
		Data:      decoded,
	}, nil
}

// extForMime maps the handful of image types a data URI declares onto the file
// extension the upload keeps, so a stored object is named like its content.
func extForMime(mime string) string {
	switch strings.ToLower(strings.TrimSpace(strings.SplitN(mime, ";", 2)[0])) {
	case "image/jpeg", "image/jpg":
		return ".jpg"
	case "image/webp":
		return ".webp"
	case "image/gif":
		return ".gif"
	default:
		return ".png"
	}
}

// referenceRequest is the reference-picture fields a generation request may
// carry, in every spelling the gateway accepts.
//
// Three names for one idea, because nothing standard covers it: `image_url` is
// what this gateway's video endpoint has always taken, `image_urls` is its
// plural, and `image` is the field OpenAI's own image-edit surface uses — which
// is what a client that already has one of these wired up will send.
//
// They are additive rather than mutually exclusive. A request that fills in two
// of them is asking for the union, and silently dropping one of a caller's
// pictures because it arrived under the other name is the kind of help nobody
// wants.
type referenceRequest struct {
	Image     string   `json:"image"`
	ImageURL  string   `json:"image_url"`
	ImageURLs []string `json:"image_urls"`
}

// collect flattens the spellings into one ordered list.
func (r referenceRequest) collect() []string {
	out := make([]string, 0, len(r.ImageURLs)+2)
	for _, raw := range append([]string{r.Image, r.ImageURL}, r.ImageURLs...) {
		if trimmed := strings.TrimSpace(raw); trimmed != "" {
			out = append(out, trimmed)
		}
	}
	return out
}

// resolveReferences turns the reference fields into turn attachments.
//
// It is the only path from a request field to a reference picture, so that the
// two generation endpoints cannot drift apart on what they accept or on how
// many they keep.
func (g *Gateway) resolveReferences(model *store.ModelConfig, request referenceRequest) ([]minimax.UploadedImage, []minimax.Adjustment, error) {
	raw := request.collect()
	keep, adjustments := referenceLimit(model, len(raw))
	raw = raw[:keep]
	images := make([]minimax.UploadedImage, 0, len(raw))
	for _, reference := range raw {
		image, err := g.resolveImage(reference)
		if err != nil {
			return nil, nil, err
		}
		images = append(images, image)
	}
	return images, adjustments, nil
}

// referenceLimit reports how many references to keep, and whether that is fewer
// than were sent.
//
// It returns a count rather than a trimmed slice because the two callers hold
// different things — one has the request fields, the other has already resolved
// attachments — and the count is the part that has to agree. It is also the
// part that gets reported: "asked for 5, used 2" is the fact, and it is gone
// once the list is trimmed.
//
// The cap is a request-size guard rather than a plugin limit: both H3 variants
// take a reference set, and the fast one was measured taking sixteen pictures in
// one turn. Keeping the first N is the right half to keep when it does bite,
// because the order the caller sends them in is the only thing that says which
// picture is the first frame.
func referenceLimit(model *store.ModelConfig, count int) (int, []minimax.Adjustment) {
	if cap := model.MaxReferences; cap > 0 && count > cap {
		return cap, []minimax.Adjustment{minimax.ReferenceAdjustment(count, cap)}
	}
	return count, nil
}

// imageName derives a filename from a URL, falling back to a stable default.
func imageName(raw string) string {
	index := strings.LastIndex(raw, "/")
	if index < 0 {
		return "image.png"
	}
	candidate := raw[index+1:]
	if query := strings.Index(candidate, "?"); query >= 0 {
		candidate = candidate[:query]
	}
	if candidate == "" {
		return "image.png"
	}
	return candidate
}

func parseContent(raw json.RawMessage) (string, []string, []contentPart) {
	if len(raw) == 0 {
		return "", nil, nil
	}
	var plain string
	if err := json.Unmarshal(raw, &plain); err == nil {
		return plain, nil, nil
	}
	var parts []contentPart
	if err := json.Unmarshal(raw, &parts); err != nil {
		return "", nil, nil
	}
	var text strings.Builder
	var urls []string
	var files []contentPart
	for _, part := range parts {
		switch part.Type {
		case "text", "input_text":
			text.WriteString(part.Text)
		case "image_url", "input_image":
			if part.ImageURL.URL != "" {
				urls = append(urls, part.ImageURL.URL)
			}
		case "file", "input_file":
			// OpenAI file parts: `file.file_id` references a /v1/files upload,
			// `file.file_data` carries an inline data URL. Kept as-is here —
			// resolving needs the gateway (the file registry), which this
			// package-level helper cannot see.
			if part.File.FileID != "" || part.File.FileData != "" {
				files = append(files, part)
			}
		}
	}
	return text.String(), urls, files
}

// ------------------------------------------------------------ image handler

// imageModelID is the catalogue entry image generation runs under.
//
// One id, not a family: image generation is a single capability upstream, so
// there is nothing to choose between. The entry still earns its place — it is
// what the console can switch off and what usage is counted against.
const imageModelID = "minimax-image"

type imageRequest struct {
	Prompt string `json:"prompt"`
	// N and Model are accepted and ignored.
	//
	// Image generation has exactly one model, and how many pictures come back
	// is the upstream's decision rather than a parameter. Honouring either
	// could therefore only ever mean rejecting the value the client guessed.
	// Read `imageModelID` for what actually runs.
	N              int    `json:"n"`
	ResponseFormat string `json:"response_format"`
	Model          string `json:"model"`
	// The reference pictures, in every spelling the gateway accepts. This is
	// what makes an image turn an edit: the pictures ride along as attachments
	// and the agent reads them, so "生成" and "参考这张改" are the same request
	// with and without them.
	referenceRequest
}

// imageModel returns the catalogue entry image generation runs under.
func (g *Gateway) imageModel() (*store.ModelConfig, error) {
	model, ok := g.store.ModelByID(imageModelID)
	if !ok || !model.Enabled {
		return nil, errors.New("image generation is disabled")
	}
	return model, nil
}

// runImageTurn sends one image request through the pool and reports what came
// back. It does no authentication, no limiting and no auditing, because it is
// called from two endpoints whose request shapes have nothing in common and
// only agree from here down.
func (g *Gateway) runImageTurn(
	ctx context.Context,
	prompt string,
	images []minimax.UploadedImage,
) (*minimax.Result, string, error) {
	settings := g.settings()
	var (
		lastErr     error
		accountName string
		result      *minimax.Result
	)

	for attempt := 0; attempt < settings.Routing.MaxAttempts; attempt++ {
		lease, err := g.pool.Acquire(ctx, "")
		if err != nil {
			lastErr = err
			break
		}
		accountName = displayName(lease.Account)
		result, err = g.client.Completion(ctx, minimax.Options{
			Credential:  CredentialOf(lease.Account),
			Text:        prompt,
			Mode:        "image",
			Images:      images,
			Timeout:     settings.RequestTimeout(),
			IdleTimeout: settings.StreamIdleTimeout(),
		})
		lease.Release(err)

		lastErr = err
		if err == nil || isPoolExhausted(err) {
			break
		}
	}

	if lastErr != nil {
		return nil, accountName, lastErr
	}
	return result, accountName, nil
}

// chatImage answers a chat completion whose model is an image model.
//
// Both ends are translations. The request is a conversation, but an image turn
// has no use for one — only the flattened prompt and any attached picture
// survive. The response is a chat message, but the product is pictures, and an
// OpenAI chat response has nowhere to put one: the only shape every client
// already renders is markdown, so that is what the pictures travel in.
//
// A turn that produced nothing is still a 200 carrying the agent's own words.
// That is deliberate and matches the video endpoint: the agent is the only
// party that knows *why* nothing came back, and turning its explanation into an
// error code would throw away the one useful thing the turn produced.
func (g *Gateway) chatImage(
	out *respWriter,
	r *http.Request,
	key *store.ClientKey,
	model *store.ModelConfig,
	request chatRequest,
	prompt string,
	images []minimax.UploadedImage,
	body []byte,
	started time.Time,
) {
	result, accountName, err := g.runImageTurn(r.Context(), prompt, images)
	if err != nil {
		g.recordAudit(r, key, model, accountName, started, upstreamStatus(err), 0, 0, 0, request.Stream, 0, string(body), "", err.Error())
		if out.wroteHeader {
			writeSSEError(out, err.Error())
			return
		}
		writeError(out, upstreamStatus(err), err.Error())
		return
	}

	urls := g.absoluteMediaURLs(r, g.persistMediaList(result.Media, prompt, model.ID, accountName))
	content := mediaMarkdown(result.Text, urls)
	promptTokens := estimateTokens(prompt)

	g.store.RecordModelUsage(model.ID, 1, 0)
	g.store.BumpClientKeyUsage(key.ID)

	if request.Stream {
		// Nothing streamed as it arrived — an image turn is one blocking call —
		// so the whole message goes out as a single chunk before the stream is
		// closed.
		ensureStreamHeaders(out)
		writeChunkRaw(out, model, map[string]any{"content": content}, nil)
		out.Flush()
		g.finishStream(out, model, nil, nil)
		g.recordAudit(r, key, model, accountName, started, http.StatusOK, 0, promptTokens, 0, true, 0, string(body), content, "")
		return
	}

	response := map[string]any{
		"id":      "chatcmpl-" + randomID(12),
		"object":  "chat.completion",
		"created": time.Now().Unix(),
		"model":   model.ID,
		"choices": []any{
			map[string]any{
				"index":         0,
				"message":       map[string]any{"role": "assistant", "content": content},
				"finish_reason": "stop",
			},
		},
		"usage": map[string]any{
			"prompt_tokens":     promptTokens,
			"completion_tokens": 0,
			"total_tokens":      promptTokens,
		},
	}
	if len(urls) > 0 {
		response["media"] = urls
	}
	raw, _ := json.Marshal(response)
	g.recordAudit(r, key, model, accountName, started, http.StatusOK, 0, promptTokens, 0, false, 0, string(body), string(raw), "")

	out.Header().Set("content-type", "application/json")
	out.WriteHeader(http.StatusOK)
	_, _ = out.Write(raw)
}

// mediaMarkdown renders one chat message out of what an image turn produced.
//
// The agent's prose goes first and the pictures after it, in that order,
// because the prose is where a refusal or a caveat lives and nobody reads a
// caveat placed underneath four images. A turn with neither still has to say
// something: an empty assistant message reads as a client-side bug and sends
// the caller looking in the wrong place.
func mediaMarkdown(prose string, urls []string) string {
	var builder strings.Builder
	if trimmed := strings.TrimSpace(prose); trimmed != "" {
		builder.WriteString(trimmed)
	}
	for _, url := range urls {
		if builder.Len() > 0 {
			builder.WriteString("\n\n")
		}
		builder.WriteString("![](" + url + ")")
	}
	if builder.Len() == 0 {
		return "The upstream turn produced neither an image nor an explanation."
	}
	return builder.String()
}

// absoluteMediaURLs makes stored media fetchable by the caller.
//
// Stored media is served from this gateway at a path, and a path is worthless
// in a chat message: the client renders markdown in its own context, where
// `/media/…` points at nothing. The configured public base URL wins when there
// is one, because that is the address the operator has declared reachable;
// otherwise the request's own origin is used, which is right both for a direct
// call and for a proxy that passes the original host through.
func (g *Gateway) absoluteMediaURLs(r *http.Request, urls []string) []string {
	base := strings.TrimRight(strings.TrimSpace(g.settings().Media.PublicBaseURL), "/")
	if base == "" {
		base = requestBase(r)
	}
	out := make([]string, 0, len(urls))
	for _, url := range urls {
		if base != "" && strings.HasPrefix(url, "/") {
			url = base + url
		}
		out = append(out, url)
	}
	return out
}

// requestBase reconstructs the origin the caller reached us on, preferring the
// proxy's view when there is one.
func requestBase(r *http.Request) string {
	if r == nil {
		return ""
	}
	scheme := "http"
	if r.TLS != nil {
		scheme = "https"
	}
	if forwarded := firstHeaderValue(r, "X-Forwarded-Proto"); forwarded == "http" || forwarded == "https" {
		scheme = forwarded
	}
	host := firstHeaderValue(r, "X-Forwarded-Host")
	if host == "" {
		host = r.Host
	}
	if host == "" {
		return ""
	}
	return scheme + "://" + host
}

// firstHeaderValue reads the first entry of a possibly comma-joined header.
func firstHeaderValue(r *http.Request, name string) string {
	raw := r.Header.Get(name)
	if index := strings.Index(raw, ","); index >= 0 {
		raw = raw[:index]
	}
	return strings.TrimSpace(raw)
}

// ImageGenerations implements POST /v1/images/generations.
func (g *Gateway) ImageGenerations(w http.ResponseWriter, r *http.Request) {
	out := &respWriter{ResponseWriter: w}
	started := time.Now()

	key, err := g.authenticate(r)
	if err != nil {
		writeError(out, http.StatusUnauthorized, err.Error())
		return
	}
	if err := g.limiter.acquire(key); err != nil {
		writeError(out, http.StatusTooManyRequests, err.Error())
		return
	}
	defer g.limiter.release(key)

	body, _ := io.ReadAll(io.LimitReader(r.Body, 4<<20))
	var request imageRequest
	if err := json.Unmarshal(body, &request); err != nil {
		writeError(out, http.StatusBadRequest, "invalid JSON body")
		return
	}
	if strings.TrimSpace(request.Prompt) == "" {
		writeError(out, http.StatusBadRequest, "prompt is required")
		return
	}

	model, err := g.imageModel()
	if err != nil {
		writeError(out, http.StatusServiceUnavailable, err.Error())
		return
	}

	images, adjustments, err := g.resolveReferences(model, request.referenceRequest)
	if err != nil {
		writeError(out, http.StatusBadRequest, err.Error())
		return
	}

	settings := g.settings()
	result, accountName, err := g.runImageTurn(r.Context(), request.Prompt, images)
	if err != nil {
		g.recordAudit(r, key, model, accountName, started, upstreamStatus(err), 0, 0, 0, false, 0, string(body), "", err.Error())
		writeError(out, upstreamStatus(err), err.Error())
		return
	}

	urls := g.persistMediaList(result.Media, request.Prompt, model.ID, accountName)
	items := make([]any, 0, len(urls))
	for index, url := range urls {
		if request.ResponseFormat == "b64_json" {
			path := filepath.Join(settings.Media.GeneratedDir, filepath.Base(url))
			if data, err := os.ReadFile(path); err == nil {
				items = append(items, map[string]any{"b64_json": base64.StdEncoding.EncodeToString(data)})
				continue
			}
		}
		source := ""
		if index < len(result.Media) {
			source = result.Media[index].URL
		}
		items = append(items, map[string]any{"url": url, "source_url": source})
	}

	g.store.RecordModelUsage(model.ID, 1, 0)
	g.store.BumpClientKeyUsage(key.ID)
	response := map[string]any{"created": time.Now().Unix(), "data": items}
	// Same contract as the video endpoint: the key is absent when nothing was
	// changed, and present only when the gateway did something the caller did
	// not ask for.
	if len(adjustments) > 0 {
		response["adjusted"] = adjustments
	}
	raw, _ := json.Marshal(response)
	g.recordAudit(r, key, model, accountName, started, http.StatusOK, 0, 0, 0, false, 0, string(body), string(raw), "")

	out.Header().Set("content-type", "application/json")
	out.WriteHeader(http.StatusOK)
	_, _ = out.Write(raw)
}

// ------------------------------------------------------------ video handler

// videoRequest is the body of POST /v1/videos/generations.
//
// The shape follows the OpenAI image endpoint rather than any MiniMax one,
// because there is no MiniMax video endpoint to follow: the parameters below
// are the four keys the plugin's own options block accepts, plus the prompt.
type videoRequest struct {
	Prompt string `json:"prompt"`
	Model  string `json:"model"`
	// Duration is whole seconds. It has no default worth guessing: the upstream
	// bills by it, so an absent value falls back to the console setting and is
	// reported back in the response.
	Duration   int    `json:"duration"`
	Ratio      string `json:"ratio"`
	Resolution string `json:"resolution"`
	// Reference frames: both H3 variants take a reference set, and the first
	// picture doubles as the first frame. Embedded rather than spelled out
	// again, so the two generation endpoints cannot disagree about what they
	// accept.
	referenceRequest
	// Reference video and audio, in the same spellings as the image fields.
	//
	// Only the slow H3.0 model takes these, and only in its multimodal
	// reference mode: it is the one variant whose tool schema carries
	// `reference_videos` and `reference_audios` at all. The fast variant
	// accepts them and then produces nothing, which is why sending them to it
	// is refused here rather than passed on — see referenceMediaModel.
	VideoURL  string   `json:"video_url"`
	VideoURLs []string `json:"video_urls"`
	AudioURL  string   `json:"audio_url"`
	AudioURLs []string `json:"audio_urls"`
	// ReferenceMode says the attachments are a reference set rather than a
	// first frame.
	//
	// It has to be stated because the two readings differ only in the text:
	// the upstream's multimodal mode is selected by the turn's wording, and a
	// picture sent without it becomes `input_image` — which the same mode
	// forbids. Without this, a reference-only turn that happens to carry
	// pictures is silently sent as a first-frame turn.
	ReferenceMode bool `json:"reference_mode"`
}

// referenceMediaModel is the one video model that takes reference video and
// audio.
//
// The check is on the upstream model rather than a catalogue flag because the
// catalogue is editable from the console and this is not a preference: the
// fast variant's tool schema has no `reference_videos` field, so a turn that
// carries one produces no file and still spends the account's credit.
const referenceMediaModel = "MiniMax-H3"

// mediaReferences collects the video and audio references from a request.
func (r videoRequest) mediaReferences() (videos, audios []string) {
	for _, raw := range append([]string{r.VideoURL}, r.VideoURLs...) {
		if trimmed := strings.TrimSpace(raw); trimmed != "" {
			videos = append(videos, trimmed)
		}
	}
	for _, raw := range append([]string{r.AudioURL}, r.AudioURLs...) {
		if trimmed := strings.TrimSpace(raw); trimmed != "" {
			audios = append(audios, trimmed)
		}
	}
	return videos, audios
}

// resolveMediaReferences turns video and audio references into attachments.
//
// Only data URIs are accepted. A picture may be named by URL because the agent
// fetches it server-side, but a reference video cannot: the upstream takes an
// attachment only with an object key owned by the turn's account, so a URL we
// host is refused and a URL we do not host is one whose bytes we cannot read.
// Saying so is better than forwarding something that fails upstream naming
// nothing.
func (g *Gateway) resolveMediaReferences(model *store.ModelConfig, request videoRequest) ([]minimax.PendingAttachment, error) {
	videos, audios := request.mediaReferences()
	if len(videos) == 0 && len(audios) == 0 {
		return nil, nil
	}
	if model.UpstreamModel != referenceMediaModel {
		return nil, fmt.Errorf(
			"reference video and audio need the %s model; %s takes pictures only",
			referenceMediaModel, model.ID)
	}
	out := make([]minimax.PendingAttachment, 0, len(videos)+len(audios))
	for _, group := range []struct {
		raws []string
		kind string
	}{{videos, "video"}, {audios, "audio"}} {
		for _, raw := range group.raws {
			attachment, err := resolveMediaAttachment(raw, group.kind)
			if err != nil {
				return nil, err
			}
			out = append(out, attachment)
		}
	}
	return out, nil
}

// resolveMediaAttachment decodes one `data:` reference into an attachment.
func resolveMediaAttachment(raw, kind string) (minimax.PendingAttachment, error) {
	if !strings.HasPrefix(raw, "data:") {
		return minimax.PendingAttachment{}, fmt.Errorf(
			"reference %s must be a data: URI — the upstream accepts an attachment only as a file it owns, so a URL cannot be forwarded (got %.60q)",
			kind, raw)
	}
	index := strings.Index(raw, ",")
	if index < 0 {
		return minimax.PendingAttachment{}, fmt.Errorf("invalid data URI for reference %s", kind)
	}
	mime := ""
	if semi := strings.Index(raw[:index], ";"); semi > 5 {
		if colon := strings.Index(raw[:semi], ":"); colon >= 0 {
			mime = raw[colon+1 : semi]
		}
	}
	if !strings.HasPrefix(mime, kind+"/") {
		return minimax.PendingAttachment{}, fmt.Errorf(
			"reference %s must carry a %s/* data URI, got %q", kind, kind, mime)
	}
	decoded, err := base64.StdEncoding.DecodeString(strings.TrimSpace(raw[index+1:]))
	if err != nil {
		return minimax.PendingAttachment{}, fmt.Errorf("invalid base64 %s payload: %w", kind, err)
	}
	if len(decoded) == 0 {
		return minimax.PendingAttachment{}, fmt.Errorf("reference %s is empty", kind)
	}
	return minimax.PendingAttachment{
		Name:     kind + "_" + randomID(12) + extForMime(mime),
		MimeType: mime,
		Size:     int64(len(decoded)),
		Data:     decoded,
	}, nil
}

// referenceInstruction states the role of each attachment.
//
// Without it the agent decides for itself, and both ways it decides wrong
// produce nothing: a picture that was meant as a reference gets used as the
// first frame, and a video or audio reference is ignored. The upstream's own
// skill describes the mode but leaves the routing to the turn's text, so the
// text has to say it.
func referenceInstruction(images, videos, audios int) string {
	roles := make([]string, 0, 3)
	if images > 0 {
		roles = append(roles, "图片作为 reference_images")
	}
	if videos > 0 {
		roles = append(roles, "视频作为 reference_videos")
	}
	if audios > 0 {
		roles = append(roles, "音频作为 reference_audios")
	}
	return "请使用多模态参考模式：附件的" + strings.Join(roles, "、") +
		"；不要把附件当作首帧（input_image）。"
}

// errEmptyVideoTurn marks a fast video turn that produced no file.
//
// The fast variant answers in the same turn or not at all, so an empty turn is
// a fact about the account — a spent balance, a connector that is not there, a
// runtime that stopped mid-sentence — and the pool is what fixes it. The slow
// variant is the opposite and is deliberately not treated this way: its
// submitting turn is *supposed* to be empty, and retrying it would submit the
// same generation to three accounts and pay for all three.
var errEmptyVideoTurn = errors.New("video turn produced no file")

// slowVideoModel is the one variant whose submitting turn is *supposed* to come
// back without a file.
//
// Every other video model answers in the same turn — H3-Max in about twenty
// seconds, Hailuo in about a minute — so an empty turn from one of them is a
// fact about the account and worth retrying elsewhere. H3.0 is the exception:
// it hands back a task id and produces the file fifteen minutes later, and
// retrying it would submit the same generation to three accounts.
const slowVideoModel = "MiniMax-H3"

// errOutOfCredit is the same situation narrowed to the one cause the turn
// names, and it exists only to pick the answer when every account tried came
// back empty: "the pool cannot afford this" is actionable, "nothing was
// produced" is not.
//
// It is the pool-wide sentinel rather than a local one because the pool has to
// recognise it as well: a spent account is parked, not blamed.
var errOutOfCredit = minimax.ErrOutOfCredit

// creditTopics are what an empty turn mentions when the reason was the
// account's balance.
//
// The check only ever chooses between two messages — it never decides whether
// to retry — so it can afford to be loose, and it has to be: the agent
// paraphrases the refusal differently every time, and the phrasings seen live
// ("账号积分不够", "停在账户余额这一关", "账户余额不够") share only the subject.
var creditTopics = []string{"积分", "余额", "insufficient account credits"}

func isOutOfCredit(text string) bool {
	return containsCreditTopic(text)
}

func containsCreditTopic(text string) bool {
	lower := strings.ToLower(text)
	for _, topic := range creditTopics {
		if strings.Contains(lower, strings.ToLower(topic)) {
			return true
		}
	}
	return false
}

// creditRefusalMarkers are the words a refusal uses around the balance topic.
// The topic alone cannot be the signal on the chat path: a perfectly good
// answer may *mention* 积分 because that is what the user asked about, and
// replaying it on two more accounts would be both slower and wrong.
var creditRefusalMarkers = []string{
	"不足", "不够", "用完", "耗尽", "已用尽", "请充值", "充值后", "升级套餐", "无法继续", "无法完成",
	"insufficient", "not enough", "out of credit", "exhausted",
}

// A refusal is a short notice, not an essay. This bounds what can be mistaken
// for one: past this length the turn produced an answer.
const maxRefusalRunes = 240

// isCreditRefusal reports whether a chat turn answered with a balance refusal
// instead of doing the work. It is deliberately stricter than isOutOfCredit,
// which only picks between two error messages.
func isCreditRefusal(text string) bool {
	trimmed := strings.TrimSpace(text)
	if trimmed == "" || utf8.RuneCountInString(trimmed) > maxRefusalRunes {
		return false
	}
	if strings.Count(trimmed, "\n") > 4 {
		return false
	}
	lower := strings.ToLower(trimmed)
	hasTopic := false
	for _, topic := range creditTopics {
		if strings.Contains(lower, strings.ToLower(topic)) {
			hasTopic = true
			break
		}
	}
	if !hasTopic {
		return false
	}
	for _, marker := range creditRefusalMarkers {
		if strings.Contains(lower, strings.ToLower(marker)) {
			return true
		}
	}
	return false
}

// isCreditRefusalError reports whether a hard upstream failure was a balance
// refusal. A 402 is the one status that means "this account, not this
// request": rejectsRequest treats it as final, which would stop the failover
// exactly when another account would have served the turn.
func isCreditRefusalError(err error) bool {
	return minimax.UpstreamStatus(err) == http.StatusPaymentRequired
}

// stallMarkers are how a model announces the step it is about to take.
var stallMarkers = []string{
	"我先", "先看", "再看看", "接下来", "然后", "让我", "我再", "现在先",
	"let me", "i'll", "i will", "next,", "next i",
}

// looksLikeStall reports a short reply that narrates an action instead of
// taking it. A long reply is an answer, and a reply that parsed a call never
// reaches this check.
func looksLikeStall(text string) bool {
	trimmed := strings.TrimSpace(text)
	if trimmed == "" || utf8.RuneCountInString(trimmed) > 240 {
		return false
	}
	lower := strings.ToLower(trimmed)
	for _, marker := range stallMarkers {
		if strings.Contains(lower, marker) {
			return true
		}
	}
	return false
}

// VideoGenerations implements POST /v1/videos/generations.
//
// This is a synchronous surface onto an asynchronous upstream. The fast H3
// variant finishes in about twenty seconds and returns a playable URL in the
// same turn, which is what this endpoint is for. The slow H3.0 model is
// documented at 15–30 minutes, and no HTTP response can hold that: the call
// returns whatever the agent has produced by the timeout — usually a task
// identifier and a status — so the caller learns the task was accepted and
// nothing was lost. Treating a slow model as if it were fast would only turn a
// usable partial answer into a gateway timeout.
func (g *Gateway) VideoGenerations(w http.ResponseWriter, r *http.Request) {
	out := &respWriter{ResponseWriter: w}
	started := time.Now()

	key, err := g.authenticate(r)
	if err != nil {
		writeError(out, http.StatusUnauthorized, err.Error())
		return
	}
	if err := g.limiter.acquire(key); err != nil {
		writeError(out, http.StatusTooManyRequests, err.Error())
		return
	}
	defer g.limiter.release(key)

	// 16MB, not 4: a reference video arrives as a data URI and base64 costs a
	// third on top, so a four-megabyte clip is already a five-and-a-half
	// megabyte body. The limit still bounds what one request can make the
	// process hold, which is the reason it exists at all.
	body, _ := io.ReadAll(io.LimitReader(r.Body, 16<<20))
	var request videoRequest
	if err := json.Unmarshal(body, &request); err != nil {
		writeError(out, http.StatusBadRequest, "invalid JSON body")
		return
	}
	if strings.TrimSpace(request.Prompt) == "" {
		writeError(out, http.StatusBadRequest, "prompt is required")
		return
	}

	settings := g.settings()

	modelID := request.Model
	if modelID == "" {
		modelID = defaultVideoModel
	}
	model, ok := g.store.ModelByID(modelID)
	if !ok || !model.Enabled || model.Type != store.ModelTypeVideo {
		writeError(out, http.StatusBadRequest, fmt.Sprintf("unknown or disabled video model %q", modelID))
		return
	}

	defaults := videoDefaults(settings, model)
	options, err := (minimax.VideoOptions{
		Model:      model.UpstreamModel,
		Ratio:      request.Ratio,
		Resolution: request.Resolution,
		Duration:   request.Duration,
	}).Normalize(defaults)
	if err != nil {
		writeError(out, http.StatusBadRequest, err.Error())
		return
	}
	// Filling the gaps is not enough on its own. The console's defaults are a
	// single global set while the ranges are per model, so a complete request
	// can still name a value this model does not offer — 2K on the 480P/768P
	// variant, or a default resolution the chosen model happens not to carry.
	// Those are repaired here rather than rejected: the plugin answers a value
	// it cannot use by asking the caller to choose, and a headless caller has
	// nobody to answer.
	options, adjustments := options.Conform(videoLimits(model), defaults)

	// Reference frames ride along as ordinary attachments; the plugin reads them
	// from the turn rather than from the options block, which only carries the
	// four generation parameters.
	images, referenceAdjustments, err := g.resolveReferences(model, request.referenceRequest)
	if err != nil {
		writeError(out, http.StatusBadRequest, err.Error())
		return
	}
	files, err := g.resolveMediaReferences(model, request)
	if err != nil {
		writeError(out, http.StatusBadRequest, err.Error())
		return
	}
	// One list, not two: a caller filtering `adjusted` by field should not have
	// to know which part of the request produced the entry. References go first
	// because they are what the caller notices — a trimmed set changes the
	// picture, a repaired resolution changes its size.
	adjustments = append(referenceAdjustments, adjustments...)

	// A turn that carries a reference video or audio is a different mode, and
	// the mode lives in the text: the plugin's schema has the fields, but which
	// attachment plays which role is the turn's to state. Pictures alone keep
	// the first-frame reading, so the sentence is only added when the mode is
	// actually multimodal — either because something other than a picture is
	// attached, or because the caller said so.
	if request.ReferenceMode && len(files) == 0 && len(images) == 0 {
		writeError(out, http.StatusBadRequest, "reference_mode needs at least one reference")
		return
	}
	text := request.Prompt
	if request.ReferenceMode || len(files) > 0 {
		videos, audios := request.mediaReferences()
		text = referenceInstruction(len(images), len(videos), len(audios)) + "\n\n" + text
	}
	prompt := g.outgoingText(settings, model, text, options)

	var (
		lastErr     error
		tried       int
		emptyTurns  int
		creditTurns int
		accountName string
		accountID   string
		usedCred    minimax.Credential
		result      *minimax.Result
	)

	for attempt := 0; attempt < settings.Routing.MaxAttempts; attempt++ {
		lease, err := g.pool.Acquire(r.Context(), "")
		if err != nil {
			lastErr = err
			break
		}
		tried++
		accountName = displayName(lease.Account)
		accountID = lease.Account.ID
		usedCred = CredentialOf(lease.Account)
		result, err = g.client.Completion(r.Context(), minimax.Options{
			Credential:   usedCred,
			Text:         prompt,
			Images:       images,
			Files:        files,
			ClientIntent: minimax.VideoClientIntent,
			Timeout:      settings.VideoTimeout(),
			IdleTimeout:  settings.StreamIdleTimeout(),
		})

		// Ask the drive before deciding the turn was empty. A turn that fell
		// back to another model delivers through a block this gateway does not
		// read, so the drive is the only sign it produced anything at all.
		if err == nil && result != nil {
			result.Media = mergeMedia(result.Media, g.collectDriveMedia(r.Context(), usedCred, result.SessionID, started.UnixMilli()))
		}

		// Every variant except the slow one answers in the same turn, so a turn
		// that produced no file is about the account and the pool is what fixes
		// it. Releasing with an error is also what parks this account so the
		// next attempt picks a different one — the loop would otherwise hand
		// the same account back, since nothing about it changed.
		//
		// Retrying is not free, but it is cheap next to the generation: a
		// refused turn measured at about fifteen credits against the hundreds a
		// video costs, and the caller already has nothing.
		if err == nil && len(result.Media) == 0 && model.UpstreamModel != slowVideoModel {
			emptyTurns++
			if isOutOfCredit(result.Text) {
				creditTurns++
			}
			lease.Release(errEmptyVideoTurn)
			continue
		}

		lastErr = err
		lease.Release(err)
		// Same rule as runTurn: a refusal is the request's fault, so trying it
		// on the next account only spends one more account.
		if err == nil || isPoolExhausted(err) || errors.Is(err, minimax.ErrUpstreamRejected) {
			break
		}
	}

	// Ran out of attempts, and every one of them came back empty saying it was
	// the balance. Handing back an empty success would read as "the model
	// produced nothing", which is a different problem with a different answer.
	if tried == settings.Routing.MaxAttempts && emptyTurns == tried && creditTurns == tried {
		g.recordAudit(r, key, model, accountName, started, http.StatusPaymentRequired, 0, 0, 0, false, 0, string(body), "", errOutOfCredit.Error())
		writeError(out, http.StatusPaymentRequired,
			"no account in the pool can afford this generation right now")
		return
	}

	// Running out of accounts mid-retry is not the caller's error: the turn it
	// already has is a real answer, and it is the one this endpoint returned
	// before the retry existed. A pool small enough to be drained by one empty
	// turn must not turn "nothing was produced" into "the gateway is broken".
	if emptyTurns > 0 && isPoolExhausted(lastErr) {
		lastErr = nil
	}

	if lastErr != nil {
		g.recordAudit(r, key, model, accountName, started, upstreamStatus(lastErr), 0, 0, 0, false, 0, string(body), "", lastErr.Error())
		writeError(out, upstreamStatus(lastErr), lastErr.Error())
		return
	}

	// The file a video turn produces is not in the stream, so the drive was
	// asked per attempt — before the loop decided whether the turn was empty.
	// See collectDriveMedia.

	urls := g.persistMediaList(result.Media, request.Prompt, model.ID, accountName)
	items := make([]any, 0, len(urls))
	for index, url := range urls {
		source := ""
		if index < len(result.Media) {
			source = result.Media[index].URL
		}
		items = append(items, map[string]any{"url": url, "source_url": source})
	}

	g.store.RecordModelUsage(model.ID, 1, 0)
	g.store.BumpClientKeyUsage(key.ID)

	response := map[string]any{
		"created": time.Now().Unix(),
		"model":   model.ID,
		"data":    items,
		// Where to come back for a file that was not ready yet.
		//
		// The drive lookup above runs once, when the turn ends, and a slow
		// model's file lands minutes after that — so a `pending` answer is not
		// the end of the story. These two are what VideoSessionMedia needs to
		// ask again, and they are reported on every answer rather than only on
		// the empty ones so a caller that does not read `status` still has
		// them.
		"session": result.SessionID,
		"account": accountID,
		// The parameters the turn actually went out with. This endpoint fills
		// in whatever the caller omitted and repairs whatever the model does not
		// accept, and neither is visible in `data` — a video at the wrong
		// resolution looks like a video. `params` is therefore the only place a
		// caller can learn what it was billed for, and `adjusted` names the
		// repairs alone, so an absent key means the request went out as written.
		"params": map[string]any{
			"model":      options.Model,
			"ratio":      options.Ratio,
			"resolution": options.Resolution,
			"duration":   options.Duration,
		},
	}
	if len(adjustments) > 0 {
		response["adjusted"] = adjustments
	}
	// A turn that produced no media still returns 200 with an empty `data` and
	// the agent's own words, because "submitted, here is the task" is a useful
	// answer and an error code is not.
	//
	// `status` is deliberately only two-valued: it reports what this turn
	// produced, which is the one thing the gateway actually knows. It does NOT
	// distinguish "a task is running" from "the upstream never executed
	// anything". That difference lives only in the agent's prose, and a live
	// check confirmed the second case is real — the account had no connector
	// tool available, the turn still cost points, and nothing was ever
	// produced. Guessing between the two by matching keywords in the prose
	// would be worse than admitting the limit, so `detail` carries the prose
	// and the caller reads it.
	if len(items) > 0 {
		response["status"] = "succeeded"
	} else {
		response["status"] = "pending"
		response["detail"] = result.Text
	}
	raw, _ := json.Marshal(response)
	g.recordAudit(r, key, model, accountName, started, http.StatusOK, 0, 0, 0, false, 0, string(body), string(raw), "")

	out.Header().Set("content-type", "application/json")
	out.WriteHeader(http.StatusOK)
	_, _ = out.Write(raw)
}

// collectDriveMedia asks the account's drive what one session produced.
//
// Two questions, in order. The summary list is where a finished turn announces
// its file. The drive listing is where the file is even when the summary says
// nothing — which is what a turn that fell back to another model looks like,
// because its delivery block never reaches this gateway. A failed lookup is
// swallowed on purpose: the turn's own result is the answer, and a broken
// lookup must not become an error the caller has to decode.
func (g *Gateway) collectDriveMedia(ctx context.Context, cred minimax.Credential, sessionID string, sinceUnixMs int64) []minimax.MediaRef {
	if sessionID == "" {
		return nil
	}
	if found, err := g.client.SessionMedia(ctx, cred, sessionID, sinceUnixMs); err == nil && len(found) > 0 {
		return found
	}
	listed, err := g.client.SessionDriveMedia(ctx, cred, sessionID, sinceUnixMs)
	if err != nil {
		return nil
	}
	return listed
}

// VideoSessionMedia implements GET /v1/videos/sessions/{session}.
//
// The second half of a slow turn. VideoGenerations asks the account's drive
// once, when the turn ends, and returns what it found — which for a model that
// takes fifteen minutes is nothing. This asks the same question again later,
// for the session and the account that call named.
//
// The caller has to name the account because a session belongs to the one that
// ran it and the pool keeps no reverse index. That is not a way in: the lookup
// runs with that account's own credentials, so naming an account you were not
// given a session for answers "session ownership" rather than a file.
func (g *Gateway) VideoSessionMedia(w http.ResponseWriter, r *http.Request) {
	out := &respWriter{ResponseWriter: w}
	started := time.Now()

	key, err := g.authenticate(r)
	if err != nil {
		writeError(out, http.StatusUnauthorized, err.Error())
		return
	}
	if err := g.limiter.acquire(key); err != nil {
		writeError(out, http.StatusTooManyRequests, err.Error())
		return
	}
	defer g.limiter.release(key)

	session := strings.TrimSpace(r.PathValue("session"))
	if session == "" {
		writeError(out, http.StatusBadRequest, "session is required")
		return
	}
	account, ok := g.store.AccountByID(strings.TrimSpace(r.URL.Query().Get("account")))
	if !ok {
		writeError(out, http.StatusBadRequest, "unknown account")
		return
	}
	cred := CredentialOf(account)

	// Everything the session ever produced, not only what is new: the caller
	// submitted the turn and has no timestamp to filter by. Repeating the call
	// is free — persistMedia hands back the entry it already has instead of
	// fetching the same file twice.
	media, err := g.client.SessionMedia(r.Context(), cred, session, 0)
	if err != nil {
		g.recordAudit(r, key, nil, displayName(account), started, http.StatusBadGateway, 0, 0, 0, false, 0, "", "", err.Error())
		writeError(out, http.StatusBadGateway, "could not read the session's files")
		return
	}

	urls := g.persistMediaList(media, "", "", displayName(account))
	items := make([]any, 0, len(urls))
	for index, url := range urls {
		source := ""
		if index < len(media) {
			source = media[index].URL
		}
		items = append(items, map[string]any{"url": url, "source_url": source})
	}

	status := "pending"
	if len(items) > 0 {
		status = "succeeded"
	}
	raw, _ := json.Marshal(map[string]any{
		"created": time.Now().Unix(),
		"session": session,
		"status":  status,
		"data":    items,
	})
	g.recordAudit(r, key, nil, displayName(account), started, http.StatusOK, 0, 0, 0, false, 0, "", string(raw), "")

	out.Header().Set("content-type", "application/json")
	out.WriteHeader(http.StatusOK)
	_, _ = out.Write(raw)
}

// defaultVideoModel is used when a caller names none. It is the fast variant:
// the only video model that can answer a synchronous request, and the cheapest
// way for a caller to discover the endpoint works.
const defaultVideoModel = "minimax-h3-max"

// outgoingText turns a prompt into the text actually sent upstream.
//
// For chat models that is the prompt itself. For video models it is a different
// kind of message entirely: the plugin reference plus the generation parameters,
// because MiniMax-H3 is not selectable as a model and the parameters have no
// place in the request body. See internal/minimax/video.go.
func (g *Gateway) outgoingText(
	settings config.Settings,
	model *store.ModelConfig,
	prompt string,
	options minimax.VideoOptions,
) string {
	if model.Type != store.ModelTypeVideo {
		return prompt
	}
	defaults := videoDefaults(settings, model)
	filled, err := options.Normalize(defaults)
	if err != nil {
		// Only reachable when the catalogue entry names no upstream model, which
		// no built-in does. The defaults are still the best answer available, and
		// a turn with an incomplete block produces nothing at all.
		filled = defaults
	}
	// A repair made on this path goes unannounced: a chat turn answers with the
	// agent's own prose and has no field to report one in. The dedicated video
	// endpoint does report it; see VideoGenerations.
	filled, _ = filled.Conform(videoLimits(model), defaults)
	return minimax.BuildVideoPrompt(prompt, settings.Video.PluginName, filled, settings.Video.OptionsTag)
}

// videoDefaults is what a video turn falls back to for a parameter the caller
// left out: the console's defaults, plus the entry's upstream model id.
func videoDefaults(settings config.Settings, model *store.ModelConfig) minimax.VideoOptions {
	return minimax.VideoOptions{
		Model:      model.UpstreamModel,
		Ratio:      settings.Video.DefaultRatio,
		Resolution: settings.Video.DefaultResolution,
		Duration:   settings.Video.DefaultDuration,
	}
}

// videoLimits narrows a catalogue entry to the ranges the gateway enforces.
func videoLimits(model *store.ModelConfig) minimax.VideoLimits {
	return minimax.VideoLimits{
		Ratios:      model.Ratios,
		Resolutions: model.Resolutions,
		Durations:   model.Durations,
	}
}

// modelObject is the `model` object the upstream's own selector sends.
//
// It is an object rather than a name because the selector also carries the
// context window and the effort level, and those belong to the model: naming a
// window a model does not offer is a refusal, which is why an entry that names
// neither leaves the decision upstream. provider_id is fixed because this
// gateway talks to exactly one provider.
func modelObject(model *store.ModelConfig) map[string]any {
	if model == nil || strings.TrimSpace(model.UpstreamModel) == "" {
		return nil
	}
	payload := map[string]any{
		"provider_id": "minimax",
		"model_id":    model.UpstreamModel,
	}
	if model.ContextWindow > 0 {
		payload["context_window"] = model.ContextWindow
	}
	if effort := strings.TrimSpace(model.Effort); effort != "" {
		payload["effort"] = effort
	}
	return payload
}

// timeoutFor picks the budget for one turn.
//
// A video turn and a chat turn have nothing in common: a chat turn that takes a
// minute is broken, and a video turn that takes a minute has not started yet.
func timeoutFor(settings config.Settings, model *store.ModelConfig) time.Duration {
	if model.Type == store.ModelTypeVideo {
		return settings.VideoTimeout()
	}
	return settings.RequestTimeout()
}

// Models implements GET /v1/models.
func (g *Gateway) Models(w http.ResponseWriter, r *http.Request) {
	if _, err := g.authenticate(r); err != nil {
		writeError(w, http.StatusUnauthorized, err.Error())
		return
	}
	data := make([]any, 0, 4)
	for _, model := range g.store.ListModels() {
		if !model.Enabled {
			continue
		}
		data = append(data, map[string]any{"id": model.ID, "object": "model", "created": 0, "owned_by": "minimax"})
	}
	writeJSON(w, http.StatusOK, map[string]any{"object": "list", "data": data})
}

// Health implements GET /health.
func (g *Gateway) Health(w http.ResponseWriter, r *http.Request) {
	total, active, cooldown, disabled, invalid, routable := g.pool.Summary()
	writeJSON(w, http.StatusOK, map[string]any{
		"status":  "ok",
		"version": Version,
		"pool": map[string]any{
			"total": total, "active": active, "cooldown": cooldown,
			"disabled": disabled, "invalid": invalid, "routable": routable,
		},
	})
}

// ------------------------------------------------------------- media helper

// mergeMedia concatenates two media lists, keeping the first occurrence of each
// URL.
//
// The stream and the drive describe the same turn from two angles, so an overlap
// is expected rather than exceptional — and a caller that receives the same video
// twice has no way to tell a duplicate from a second render.
func mergeMedia(primary, extra []minimax.MediaRef) []minimax.MediaRef {
	if len(extra) == 0 {
		return primary
	}
	seen := make(map[string]bool, len(primary)+len(extra))
	out := make([]minimax.MediaRef, 0, len(primary)+len(extra))
	for _, list := range [][]minimax.MediaRef{primary, extra} {
		for _, item := range list {
			if item.URL == "" || seen[item.URL] {
				continue
			}
			seen[item.URL] = true
			out = append(out, item)
		}
	}
	return out
}

func (g *Gateway) persistMediaList(media []minimax.MediaRef, prompt, model, account string) []string {
	urls := make([]string, 0, len(media))
	for _, item := range media {
		urls = append(urls, g.persistMedia(item, prompt, model, account))
	}
	return urls
}

func (g *Gateway) persistMedia(media minimax.MediaRef, prompt, model, account string) string {
	// Idempotent by source. The same file gets asked for more than once — a
	// slow turn is polled for its result, and every poll reads the same drive
	// entry — and a second record would both download the file again and put
	// the same video in the gallery twice.
	if existing, ok := g.store.MediaBySource(media.URL); ok {
		return existing.URL
	}
	settings := g.settings()
	item := &store.MediaItem{
		ID:          "media_" + randomID(8),
		Kind:        media.Kind,
		SourceURL:   media.URL,
		URL:         media.URL,
		Prompt:      prompt,
		Model:       model,
		AccountName: account,
		CreatedAt:   time.Now(),
	}

	if settings.Media.AutoDownload {
		extension := ".bin"
		switch media.Kind {
		case "image":
			extension = ".png"
		case "video":
			extension = ".mp4"
		}
		filename := item.ID + extension
		target := filepath.Join(settings.Media.GeneratedDir, filename)

		// Downloads go through the same proxy as the API. Generated media is
		// served from MiniMax's CDN, and the account's egress fence applies to it
		// too — so fetching it from the local address fails the same way an
		// unproxied API call does, as a reset that looks like the CDN is down.
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, media.URL, nil)
		if err == nil {
			if resp, err := minimax.DownloadClient(settings).Do(req); err == nil {
				if resp.StatusCode < 400 {
					if file, err := os.Create(target); err == nil {
						if _, err := io.Copy(file, io.LimitReader(resp.Body, 256<<20)); err == nil {
							item.URL = "/media/" + filename
						} else {
							_ = os.Remove(target)
						}
						_ = file.Close()
					}
				}
				_ = resp.Body.Close()
			}
		}
		cancel()
	}

	g.store.AddMedia(item)
	return item.URL
}

// --------------------------------------------------------------- audit glue

func (g *Gateway) recordAudit(
	r *http.Request,
	key *store.ClientKey,
	model *store.ModelConfig,
	accountName string,
	started time.Time,
	status int,
	firstTokenMs int64,
	promptTokens, completionTokens int,
	stream bool,
	retries int,
	requestBody, responseBody, errText string,
) {
	settings := g.settings()
	// A request that is not about one model has none to name — the session
	// lookup reads a file the turn already produced — and inventing one would
	// put a model in the audit that never ran.
	modelID := ""
	if model != nil {
		modelID = model.ID
	}
	audit := &store.Audit{
		ID:               "req_" + randomID(10),
		CreatedAt:        time.Now(),
		KeyName:          key.Name,
		Model:            modelID,
		AccountName:      accountName,
		Status:           status,
		LatencyMs:        time.Since(started).Milliseconds(),
		FirstTokenMs:     firstTokenMs,
		PromptTokens:     promptTokens,
		CompletionTokens: completionTokens,
		Stream:           stream,
		Retries:          retries,
		IP:               clientIP(r),
		UserAgent:        r.Header.Get("user-agent"),
		Error:            errText,
	}
	if settings.Audit.RecordBody {
		audit.RequestBody = clampBody(requestBody, settings.Audit.BodyLimitBytes)
		audit.ResponseBody = clampBody(responseBody, settings.Audit.BodyLimitBytes)
	}
	g.store.AppendAudit(audit)
}

func clampBody(value string, limit int) string {
	if limit <= 0 || len(value) <= limit {
		return value
	}
	return value[:limit] + "…"
}

// ------------------------------------------------------------------ helpers

func (g *Gateway) authenticate(r *http.Request) (*store.ClientKey, error) {
	raw := strings.TrimSpace(r.Header.Get("authorization"))
	if raw == "" {
		raw = strings.TrimSpace(r.Header.Get("x-api-key"))
	}
	if len(raw) > 7 && strings.EqualFold(raw[:7], "bearer ") {
		raw = strings.TrimSpace(raw[7:])
	}
	if raw == "" {
		return nil, fmt.Errorf("missing API key")
	}
	key, ok := g.store.ClientKeyByValue(raw)
	if !ok {
		return nil, fmt.Errorf("invalid API key")
	}
	if !key.Enabled {
		return nil, fmt.Errorf("API key disabled")
	}
	return key, nil
}

// CredentialOf projects a pooled account into the upstream identity bundle.
// It is exported because the admin console probes accounts with the same
// mapping, and a second copy of these eight fields would be a second place to
// forget when the protocol grows a field.
func CredentialOf(account *store.Account) minimax.Credential {
	return minimax.Credential{
		Region:       account.Region,
		Token:        account.Token,
		UserID:       account.UserID,
		AgentID:      account.AgentID,
		DeviceID:     account.DeviceID,
		UUID:         account.UUID,
		ScreenWidth:  account.ScreenWidth,
		ScreenHeight: account.ScreenHeight,
		BaseURL:      account.BaseURL,
	}
}

func displayName(account *store.Account) string {
	if account.Name != "" {
		return account.Name
	}
	if account.Identifier != "" {
		return account.Identifier
	}
	return store.MaskToken(account.Token)
}

func isPoolExhausted(err error) bool {
	if err == nil {
		return false
	}
	message := err.Error()
	return strings.Contains(message, "no account available") || strings.Contains(message, "all accounts are busy")
}

// upstreamStatus maps a turn failure onto the status the caller should see.
//
// A request the upstream refused is the caller's problem, so the upstream's own
// status travels back and the caller can fix what it sent. Everything else —
// a dead account, an exhausted pool, a 5xx — is a gateway failure and stays a
// 502, because the caller cannot do anything about it.
func upstreamStatus(err error) int {
	// Every account the pool tried was spent. That is a payment problem on the
	// provider's side, not a gateway failure, and 402 says so without a body
	// full of prose.
	if errors.Is(err, minimax.ErrOutOfCredit) {
		return http.StatusPaymentRequired
	}
	if status := minimax.UpstreamStatus(err); status != 0 {
		return status
	}
	return http.StatusBadGateway
}

func clientIP(r *http.Request) string {
	if forwarded := r.Header.Get("x-forwarded-for"); forwarded != "" {
		return strings.TrimSpace(strings.Split(forwarded, ",")[0])
	}
	host := r.RemoteAddr
	if index := strings.LastIndex(host, ":"); index >= 0 {
		host = host[:index]
	}
	return host
}

func randomID(length int) string {
	buf := make([]byte, (length+1)/2)
	if _, err := rand.Read(buf); err != nil {
		return fmt.Sprintf("%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(buf)[:length]
}

func estimateTokens(text string) int {
	if text == "" {
		return 0
	}
	return len([]rune(text))/2 + 1
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	raw, err := json.Marshal(payload)
	if err != nil {
		http.Error(w, "encode failed", http.StatusInternalServerError)
		return
	}
	w.Header().Set("content-type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(raw)
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]any{"error": map[string]any{"message": message, "type": "minimax2api_error"}})
}

func writeSSEError(w http.ResponseWriter, message string) {
	raw, _ := json.Marshal(map[string]any{"error": map[string]any{"message": message}})
	_, _ = fmt.Fprintf(w, "data: %s\n\ndata: [DONE]\n\n", raw)
}
