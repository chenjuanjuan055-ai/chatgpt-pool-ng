package gateway

import (
	"bufio"
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"slices"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"minimax2api/internal/config"
	"minimax2api/internal/minimax"
	"minimax2api/internal/pool"
	"minimax2api/internal/store"
)

// These tests drive the full request path - auth, limiting, pool scheduling,
// upstream parsing, audit - against a stub upstream, so the gateway can be
// exercised end to end without a live MiniMax credential.

type harness struct {
	gateway  *Gateway
	store    *store.Store
	pool     *pool.Pool
	upstream *httptest.Server
	settings *config.Settings
	key      string
}

// upstreamText builds the SSE body a normal answer produces.
//
// The adapter dispatches on payload keys rather than on the numeric frame
// types, so this pins the documented shape without depending on the event enum.
func upstreamText(text string) string {
	chunk, _ := json.Marshal(map[string]any{
		"type":                6,
		"agent_message_chunk": map[string]any{"msg_content": text},
	})
	return "data:" + string(chunk) + "\n\n" +
		`data:{"type":9,"finish_reason":"stop"}` + "\n\n"
}

// upstreamThinking builds a reply that carries a separate reasoning stream.
func upstreamThinking(reasoning, text string) string {
	think, _ := json.Marshal(map[string]any{
		"type":           4,
		"thinking_chunk": map[string]any{"reasoning_content": reasoning},
	})
	answer, _ := json.Marshal(map[string]any{
		"type":                6,
		"agent_message_chunk": map[string]any{"msg_content": text},
	})
	return "data:" + string(think) + "\n\n" + "data:" + string(answer) + "\n\n"
}

// upstreamMedia builds a reply that produced media attachments.
func upstreamMedia(urls ...string) string {
	items := make([]any, 0, len(urls))
	for _, url := range urls {
		items = append(items, map[string]any{"image_url": url})
	}
	chunk, _ := json.Marshal(map[string]any{
		"type":                6,
		"agent_message_chunk": map[string]any{"msg_content": ""},
		"media":               items,
	})
	return "data:" + string(chunk) + "\n\n"
}

// upstreamFunc builds the stub upstream.
//
// The adapter makes two calls per completion - a session handshake and then the
// message stream - and the token travels in a header on both. Modelling a
// rejected credential therefore means answering 401 to the handshake, which is
// what accept returning false does. accept sees every request; stream only sees
// the message call.
func upstreamFunc(accept func(token string) bool, stream func(token string) string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		token := r.Header.Get("token")
		if accept != nil && !accept(token) {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		if strings.HasSuffix(r.URL.Path, "/message") {
			w.Header().Set("content-type", "text/event-stream")
			_, _ = io.WriteString(w, stream(token))
			return
		}
		w.Header().Set("content-type", "application/json")
		_, _ = io.WriteString(w, `{"session_id":"sess_test"}`)
	}
}

// acceptsEverything is the common stub: every credential works and every answer
// is the same body.
func acceptsEverything(body string) http.HandlerFunc {
	return upstreamFunc(nil, func(string) string { return body })
}

func newHarness(t *testing.T, upstream http.HandlerFunc) *harness {
	t.Helper()

	dir := t.TempDir()
	st, err := store.Open(dir, "admin", "admin12345")
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { _ = st.Close() })

	settings := config.DefaultSettings(dir)
	settings.Routing.CapacityWaitSec = 0 // fail fast instead of waiting for capacity
	settings.Routing.MaxAttempts = 3
	settings.Upstream.RequestTimeoutSec = 5
	settings.Upstream.StreamIdleTimeoutSec = 3
	settings.Media.AutoDownload = false

	server := httptest.NewServer(upstream)
	t.Cleanup(server.Close)
	settings.Upstream.BaseURL = server.URL

	settingsFn := func() config.Settings { return settings }
	client := minimax.New(settingsFn)
	p := pool.New(st, settingsFn)
	gw := New(st, p, client, settingsFn)

	key, err := st.CreateClientKey("test-key", 0, 0)
	if err != nil {
		t.Fatalf("create client key: %v", err)
	}

	return &harness{
		gateway:  gw,
		store:    st,
		pool:     p,
		upstream: server,
		settings: &settings,
		key:      key.Key,
	}
}

// addAccount inserts a routable account. The fingerprint is derived from the
// name so no two accounts in a test share one.
func (h *harness) addAccount(t *testing.T, name, token string, priority int) *store.Account {
	t.Helper()
	account := &store.Account{
		Name: name, Token: token, Region: store.RegionGlobal,
		UUID: "uuid-" + name, DeviceID: "device-" + name,
		// Prepared, because an account that is not is held out of rotation: a
		// missing realUserID answers a bare 401, which this pool reads as a dead
		// token, and a missing agent id opens no session at all.
		UserID: "1", AgentID: "443154487857417",
		Priority: priority, MaxConcurrent: 2, Enabled: true,
	}
	if err := h.store.AddAccount(account); err != nil {
		t.Fatalf("add account %s: %v", name, err)
	}
	return account
}

func (h *harness) chat(t *testing.T, body map[string]any, token string) *httptest.ResponseRecorder {
	t.Helper()
	raw, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewReader(raw))
	req.Header.Set("content-type", "application/json")
	if token != "" {
		req.Header.Set("authorization", "Bearer "+token)
	}
	rec := httptest.NewRecorder()
	h.gateway.ChatCompletions(rec, req)
	return rec
}

func decodeJSON(t *testing.T, rec *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("response is not JSON: %v\nbody: %s", err, rec.Body.String())
	}
	return out
}

// sseDataLines pulls every `data:` payload out of an SSE response.
func sseDataLines(t *testing.T, rec *httptest.ResponseRecorder) []string {
	t.Helper()
	var out []string
	scanner := bufio.NewScanner(strings.NewReader(rec.Body.String()))
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "data:") {
			out = append(out, strings.TrimSpace(strings.TrimPrefix(line, "data:")))
		}
	}
	return out
}

func TestChatCompletionsRequiresClientKey(t *testing.T) {
	h := newHarness(t, func(w http.ResponseWriter, r *http.Request) {
		t.Error("upstream must not be called without a valid key")
	})

	rec := h.chat(t, map[string]any{"model": "minimax-agent", "messages": []any{map[string]any{"role": "user", "content": "hi"}}}, "")
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", rec.Code)
	}
}

func TestChatCompletionsRejectsUnknownKey(t *testing.T) {
	h := newHarness(t, func(w http.ResponseWriter, r *http.Request) {
		t.Error("upstream must not be called with an unknown key")
	})

	rec := h.chat(t, map[string]any{"model": "minimax-agent", "messages": []any{map[string]any{"role": "user", "content": "hi"}}}, "sk-mm-wrong")
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", rec.Code)
	}
}

func TestChatCompletionsRejectsUnknownModel(t *testing.T) {
	h := newHarness(t, func(w http.ResponseWriter, r *http.Request) {
		t.Error("upstream must not be called for an unknown model")
	})

	rec := h.chat(t, map[string]any{"model": "gpt-4", "messages": []any{map[string]any{"role": "user", "content": "hi"}}}, h.key)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "model") {
		t.Fatalf("error should name the model: %s", rec.Body.String())
	}
}

func TestChatCompletionsReturnsOpenAIResponse(t *testing.T) {
	h := newHarness(t, upstreamFunc(
		func(token string) bool {
			if token != "token-good" {
				t.Errorf("token = %q, want the pooled account credential", token)
			}
			return true
		},
		func(string) string { return upstreamText("你好，世界") },
	))
	h.addAccount(t, "primary", "token-good", 10)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-agent",
		"messages": []any{map[string]any{"role": "user", "content": "打个招呼"}},
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	payload := decodeJSON(t, rec)

	if payload["object"] != "chat.completion" {
		t.Fatalf("object = %v", payload["object"])
	}
	if payload["model"] != "minimax-agent" {
		t.Fatalf("model = %v", payload["model"])
	}
	choices, _ := payload["choices"].([]any)
	if len(choices) != 1 {
		t.Fatalf("choices = %v", payload["choices"])
	}
	choice, _ := choices[0].(map[string]any)
	message, _ := choice["message"].(map[string]any)
	if message["content"] != "你好，世界" {
		t.Fatalf("content = %v", message["content"])
	}
	if message["role"] != "assistant" {
		t.Fatalf("role = %v", message["role"])
	}
	if choice["finish_reason"] != "stop" {
		t.Fatalf("finish_reason = %v", choice["finish_reason"])
	}
	usage, _ := payload["usage"].(map[string]any)
	if usage["total_tokens"] == nil {
		t.Fatalf("usage missing: %v", payload["usage"])
	}

	// The pool must hand the account back after the request.
	if got := h.pool.Inflight(accountID(t, h, "primary")); got != 0 {
		t.Fatalf("inflight = %d after the request, want 0", got)
	}
}

// Reasoning arrives on its own frame and must land in reasoning_content rather
// than being concatenated into the answer.
func TestChatCompletionsSeparatesReasoning(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamThinking("先想一下", "答案是 42")))
	h.addAccount(t, "primary", "token-good", 10)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-m3-thinking",
		"messages": []any{map[string]any{"role": "user", "content": "6*7?"}},
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	payload := decodeJSON(t, rec)
	choices, _ := payload["choices"].([]any)
	choice, _ := choices[0].(map[string]any)
	message, _ := choice["message"].(map[string]any)
	if message["content"] != "答案是 42" {
		t.Fatalf("content = %v, want only the answer", message["content"])
	}
	if message["reasoning_content"] != "先想一下" {
		t.Fatalf("reasoning_content = %v", message["reasoning_content"])
	}
}

func TestChatCompletionsStreamsSSE(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("流式回答")))
	h.addAccount(t, "primary", "token-good", 10)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-agent",
		"messages": []any{map[string]any{"role": "user", "content": "hi"}},
		"stream":   true,
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if ct := rec.Header().Get("content-type"); !strings.Contains(ct, "text/event-stream") {
		t.Fatalf("content-type = %q", ct)
	}
	// Proxies must not buffer a stream.
	if rec.Header().Get("x-accel-buffering") != "no" {
		t.Fatalf("x-accel-buffering = %q", rec.Header().Get("x-accel-buffering"))
	}

	lines := sseDataLines(t, rec)
	if len(lines) < 3 {
		t.Fatalf("expected several SSE chunks, got %v", lines)
	}
	if lines[len(lines)-1] != "[DONE]" {
		t.Fatalf("stream must end with [DONE], got %q", lines[len(lines)-1])
	}

	var sawRole, sawContent, sawStop bool
	for _, line := range lines {
		if line == "[DONE]" {
			continue
		}
		var chunk map[string]any
		if err := json.Unmarshal([]byte(line), &chunk); err != nil {
			t.Fatalf("chunk is not JSON: %v (%q)", err, line)
		}
		if chunk["object"] != "chat.completion.chunk" {
			t.Fatalf("object = %v", chunk["object"])
		}
		choices, _ := chunk["choices"].([]any)
		if len(choices) != 1 {
			t.Fatalf("choices = %v", choices)
		}
		entry, _ := choices[0].(map[string]any)
		delta, _ := entry["delta"].(map[string]any)
		if delta["role"] == "assistant" {
			sawRole = true
		}
		if delta["content"] == "流式回答" {
			sawContent = true
		}
		if entry["finish_reason"] == "stop" {
			sawStop = true
		}
	}
	if !sawRole || !sawContent || !sawStop {
		t.Fatalf("missing frames: role=%v content=%v stop=%v", sawRole, sawContent, sawStop)
	}
}

// A rejected credential must be marked invalid and the request retried on the
// next account in the pool.
func TestChatCompletionsFailsOverToHealthyAccount(t *testing.T) {
	var attempts []string
	h := newHarness(t, upstreamFunc(
		func(token string) bool {
			attempts = append(attempts, token)
			return token != "token-bad"
		},
		func(string) string { return upstreamText("换号成功") },
	))

	bad := h.addAccount(t, "broken", "token-bad", 1) // picked first
	h.addAccount(t, "healthy", "token-good", 50)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-agent",
		"messages": []any{map[string]any{"role": "user", "content": "hi"}},
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 after failover; body = %s", rec.Code, rec.Body.String())
	}
	payload := decodeJSON(t, rec)
	choices, _ := payload["choices"].([]any)
	choice, _ := choices[0].(map[string]any)
	message, _ := choice["message"].(map[string]any)
	if message["content"] != "换号成功" {
		t.Fatalf("content = %v", message["content"])
	}

	// The rejected account fails at the handshake, so it contributes exactly one
	// attempt and the healthy account is tried afterwards.
	if len(attempts) == 0 || attempts[0] != "token-bad" {
		t.Fatalf("attempts = %v, want the rejected account tried first", attempts)
	}
	if !slices.Contains(attempts, "token-good") {
		t.Fatalf("attempts = %v, want a retry on the healthy account", attempts)
	}

	// The rejected account must be quarantined so it stops being scheduled.
	updated, ok := h.store.AccountByID(bad.ID)
	if !ok {
		t.Fatal("account disappeared")
	}
	if updated.Status != store.StatusInvalid {
		t.Fatalf("status = %q, want %q after a 401", updated.Status, store.StatusInvalid)
	}
	if updated.LastError == "" {
		t.Fatal("LastError should explain why the account was quarantined")
	}
}

func TestChatCompletionsReportsNoAccount(t *testing.T) {
	h := newHarness(t, func(w http.ResponseWriter, r *http.Request) {
		t.Error("upstream must not be called when the pool is empty")
	})

	rec := h.chat(t, map[string]any{
		"model":    "minimax-agent",
		"messages": []any{map[string]any{"role": "user", "content": "hi"}},
	}, h.key)

	if rec.Code != http.StatusBadGateway {
		t.Fatalf("status = %d, want 502", rec.Code)
	}
}

func TestChatCompletionsRecordsAudit(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("审计")))
	h.addAccount(t, "primary", "token-good", 10)

	if rec := h.chat(t, map[string]any{
		"model":    "minimax-agent",
		"messages": []any{map[string]any{"role": "user", "content": "hi"}},
	}, h.key); rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}

	audits := h.store.ListAudits()
	if len(audits) != 1 {
		t.Fatalf("audits = %d, want 1", len(audits))
	}
	audit := audits[0]
	if audit.Status != http.StatusOK {
		t.Fatalf("audit status = %d", audit.Status)
	}
	if audit.Model != "minimax-agent" {
		t.Fatalf("audit model = %q", audit.Model)
	}
	if audit.AccountName != "primary" {
		t.Fatalf("audit account = %q", audit.AccountName)
	}
	if audit.LatencyMs < 0 {
		t.Fatalf("audit latency = %d", audit.LatencyMs)
	}
}

// A failed call must still be audited, with the upstream reason attached.
func TestChatCompletionsAuditsFailures(t *testing.T) {
	h := newHarness(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
		_, _ = fmt.Fprint(w, "upstream is down")
	})
	h.addAccount(t, "primary", "token-good", 10)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-agent",
		"messages": []any{map[string]any{"role": "user", "content": "hi"}},
	}, h.key)
	if rec.Code != http.StatusBadGateway {
		t.Fatalf("status = %d, want 502", rec.Code)
	}

	audits := h.store.ListAudits()
	if len(audits) == 0 {
		t.Fatal("a failed request must still produce an audit record")
	}
	if audits[0].Error == "" {
		t.Fatal("audit should carry the upstream error")
	}
}

// A refusal from the upstream belongs to the caller.
//
// It has to travel back with the upstream's own status, it must not be retried
// on another account, and it must not cost the account that happened to carry
// it. Before this existed, a 400 was reported as a 502 and retried MaxAttempts
// times, so one caller sending an unsupported attachment URL put three accounts
// into cooldown per request.
func TestChatCompletionsPassesUpstreamRefusalThrough(t *testing.T) {
	var calls atomic.Int64
	h := newHarness(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusBadRequest)
		_, _ = fmt.Fprint(w, `{"key":"invalid_params","detail":"attachment requires one owned object_key"}`)
	})
	h.addAccount(t, "primary", "token-good", 10)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-agent",
		"messages": []any{map[string]any{"role": "user", "content": "hi"}},
	}, h.key)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400 (the upstream's own refusal)", rec.Code)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("upstream calls = %d, want 1 (a refusal is not retried)", got)
	}

	accounts := h.store.ListAccounts()
	if len(accounts) != 1 {
		t.Fatalf("accounts = %d, want 1", len(accounts))
	}
	if account := accounts[0]; account.Status != store.StatusActive || account.FailCount != 0 {
		t.Fatalf("account = %s/failCount %d, want active/0 (the account is not at fault)",
			account.Status, account.FailCount)
	}

	audits := h.store.ListAudits()
	if len(audits) == 0 {
		t.Fatal("a refusal must still be audited")
	}
	if audits[0].Status != http.StatusBadRequest {
		t.Fatalf("audit status = %d, want 400", audits[0].Status)
	}
}

func TestChatCompletionsEnforcesRateLimit(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("ok")))
	h.addAccount(t, "primary", "token-good", 10)

	// Replace the unlimited key with a 1 RPM one.
	limited, err := h.store.CreateClientKey("limited", 1, 4)
	if err != nil {
		t.Fatalf("create limited key: %v", err)
	}

	body := map[string]any{"model": "minimax-agent", "messages": []any{map[string]any{"role": "user", "content": "hi"}}}
	if rec := h.chat(t, body, limited.Key); rec.Code != http.StatusOK {
		t.Fatalf("first call status = %d", rec.Code)
	}
	rec := h.chat(t, body, limited.Key)
	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("second call status = %d, want 429", rec.Code)
	}
}

func TestModelsEndpoint(t *testing.T) {
	h := newHarness(t, func(w http.ResponseWriter, r *http.Request) {})

	req := httptest.NewRequest(http.MethodGet, "/v1/models", nil)
	rec := httptest.NewRecorder()
	h.gateway.Models(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("anonymous status = %d, want 401", rec.Code)
	}

	req = httptest.NewRequest(http.MethodGet, "/v1/models", nil)
	req.Header.Set("authorization", "Bearer "+h.key)
	rec = httptest.NewRecorder()
	h.gateway.Models(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}

	payload := decodeJSON(t, rec)
	if payload["object"] != "list" {
		t.Fatalf("object = %v", payload["object"])
	}
	data, _ := payload["data"].([]any)
	if len(data) == 0 {
		t.Fatal("model list is empty")
	}
	ids := map[string]bool{}
	for _, item := range data {
		entry, _ := item.(map[string]any)
		id, _ := entry["id"].(string)
		ids[id] = true
		if entry["object"] != "model" {
			t.Fatalf("entry object = %v", entry["object"])
		}
	}
	for _, want := range []string{"minimax-agent", "minimax-m3-thinking", "minimax-image"} {
		if !ids[want] {
			t.Fatalf("model list is missing %s: %v", want, ids)
		}
	}
}

func TestHealthReportsPoolSummary(t *testing.T) {
	h := newHarness(t, func(w http.ResponseWriter, r *http.Request) {})
	h.addAccount(t, "one", "token-a", 10)
	h.addAccount(t, "two", "token-b", 20)

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rec := httptest.NewRecorder()
	h.gateway.Health(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}
	payload := decodeJSON(t, rec)
	if payload["status"] != "ok" {
		t.Fatalf("status = %v", payload["status"])
	}
	summary, _ := payload["pool"].(map[string]any)
	if summary["total"] != float64(2) {
		t.Fatalf("pool total = %v, want 2", summary["total"])
	}
	if summary["routable"] != float64(2) {
		t.Fatalf("pool routable = %v, want 2", summary["routable"])
	}
}

func TestImageGenerationsReturnsMedia(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamMedia("https://cdn/one.png", "https://cdn/two.png")))
	h.addAccount(t, "primary", "token-good", 10)

	raw, _ := json.Marshal(map[string]any{"prompt": "a blue circle", "n": 2})
	req := httptest.NewRequest(http.MethodPost, "/v1/images/generations", bytes.NewReader(raw))
	req.Header.Set("content-type", "application/json")
	req.Header.Set("authorization", "Bearer "+h.key)
	rec := httptest.NewRecorder()
	h.gateway.ImageGenerations(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	payload := decodeJSON(t, rec)
	data, _ := payload["data"].([]any)
	if len(data) != 2 {
		t.Fatalf("data = %v, want 2 images", payload["data"])
	}
	first, _ := data[0].(map[string]any)
	if first["url"] != "https://cdn/one.png" {
		t.Fatalf("url = %v", first["url"])
	}
}

// A client cannot learn from /v1/models that an image model is an image model —
// the schema has nowhere to say so — so picking it in a chat window sends the
// prompt to the chat endpoint. The answer therefore has to arrive as something
// the client already knows how to render.
func TestChatCompletionsAnswersAnImageModelWithMarkdown(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamMedia("https://cdn/cat.png")))
	h.addAccount(t, "primary", "token-good", 10)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-image",
		"messages": []any{map[string]any{"role": "user", "content": "一只小猫"}},
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	payload := decodeJSON(t, rec)
	if payload["model"] != "minimax-image" {
		t.Fatalf("model = %v", payload["model"])
	}
	choices, _ := payload["choices"].([]any)
	if len(choices) != 1 {
		t.Fatalf("choices = %v", payload["choices"])
	}
	choice, _ := choices[0].(map[string]any)
	message, _ := choice["message"].(map[string]any)
	content, _ := message["content"].(string)
	if !strings.Contains(content, "![](https://cdn/cat.png)") {
		t.Fatalf("content does not render the image: %q", content)
	}
	if media, _ := payload["media"].([]any); len(media) != 1 || media[0] != "https://cdn/cat.png" {
		t.Fatalf("media = %v", payload["media"])
	}

	// The turn is billed to the image entry, not to whatever the caller named.
	entry, ok := h.store.ModelByID("minimax-image")
	if !ok || entry.Requests != 1 {
		t.Fatalf("usage was not recorded against the image entry: %+v", entry)
	}
}

// An image turn is one blocking call, so there is nothing to stream as it
// arrives — but the client asked for SSE and has to get a well-formed one.
func TestChatCompletionsStreamsAnImageModelAsOneChunk(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamMedia("https://cdn/cat.png")))
	h.addAccount(t, "primary", "token-good", 10)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-image",
		"stream":   true,
		"messages": []any{map[string]any{"role": "user", "content": "一只小猫"}},
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if ct := rec.Header().Get("content-type"); !strings.Contains(ct, "text/event-stream") {
		t.Fatalf("content-type = %q", ct)
	}

	lines := sseDataLines(t, rec)
	if len(lines) == 0 || lines[len(lines)-1] != "[DONE]" {
		t.Fatalf("stream does not end with [DONE]: %v", lines)
	}
	var streamed string
	var sawStop bool
	for _, line := range lines {
		if line == "[DONE]" {
			continue
		}
		var payload map[string]any
		if err := json.Unmarshal([]byte(line), &payload); err != nil {
			t.Fatalf("chunk is not JSON: %v", err)
		}
		choices, _ := payload["choices"].([]any)
		if len(choices) == 0 {
			continue
		}
		choice, _ := choices[0].(map[string]any)
		if choice["finish_reason"] == "stop" {
			sawStop = true
		}
		delta, _ := choice["delta"].(map[string]any)
		if text, ok := delta["content"].(string); ok {
			streamed += text
		}
	}
	if !strings.Contains(streamed, "![](https://cdn/cat.png)") {
		t.Fatalf("streamed content = %q", streamed)
	}
	if !sawStop {
		t.Fatal("no chunk reported finish_reason stop")
	}
}

// A turn that produced nothing is still an answer. The agent is the only party
// that knows why, so its words are the payload and an error code would discard
// them — the same rule the video endpoint follows.
func TestChatImageKeepsTheAgentsExplanation(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("I cannot reach the image service.")))
	h.addAccount(t, "primary", "token-good", 10)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-image",
		"messages": []any{map[string]any{"role": "user", "content": "一只小猫"}},
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	payload := decodeJSON(t, rec)
	choices, _ := payload["choices"].([]any)
	choice, _ := choices[0].(map[string]any)
	message, _ := choice["message"].(map[string]any)
	if content, _ := message["content"].(string); !strings.Contains(content, "I cannot reach") {
		t.Fatalf("the agent's explanation was dropped: %v", message["content"])
	}
	if _, present := payload["media"]; present {
		t.Fatalf("media should be absent when nothing was produced: %v", payload["media"])
	}
}

func TestMediaMarkdownPutsTheProseAboveThePictures(t *testing.T) {
	cases := []struct {
		name  string
		prose string
		urls  []string
		want  string
	}{
		{"prose only", "I cannot do that.", nil, "I cannot do that."},
		{"picture only", "", []string{"https://cdn/a.png"}, "![](https://cdn/a.png)"},
		{
			"both", "Here you go.", []string{"https://cdn/a.png", "https://cdn/b.png"},
			"Here you go.\n\n![](https://cdn/a.png)\n\n![](https://cdn/b.png)",
		},
		{"blank prose is not content", " \n\t ", []string{"https://cdn/a.png"}, "![](https://cdn/a.png)"},
		{"nothing at all", "", nil, "The upstream turn produced neither an image nor an explanation."},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := mediaMarkdown(tc.prose, tc.urls); got != tc.want {
				t.Fatalf("mediaMarkdown = %q, want %q", got, tc.want)
			}
		})
	}
}

// Stored media is served from this gateway at a path, and a path means nothing
// to a client rendering markdown in its own context.
func TestAbsoluteMediaURLsMakesStoredFilesFetchable(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("never reached")))

	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	req.Host = "gateway.example:8080"

	got := h.gateway.absoluteMediaURLs(req, []string{"/media/a.png", "https://cdn/b.png"})
	if got[0] != "http://gateway.example:8080/media/a.png" {
		t.Fatalf("stored media = %q", got[0])
	}
	if got[1] != "https://cdn/b.png" {
		t.Fatalf("an absolute source url must be left alone: %q", got[1])
	}
}

func TestAbsoluteMediaURLsPrefersTheProxyAndThenTheConfiguredBase(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("never reached")))

	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	req.Host = "internal:8080"
	req.Header.Set("X-Forwarded-Proto", "https")
	req.Header.Set("X-Forwarded-Host", "m2api.example")
	if got := h.gateway.absoluteMediaURLs(req, []string{"/media/a.png"}); got[0] != "https://m2api.example/media/a.png" {
		t.Fatalf("proxied origin = %q", got[0])
	}

	// The configured base wins over both, because it is the address the
	// operator has declared reachable.
	h.settings.Media.PublicBaseURL = "https://cdn.example/public/"
	if got := h.gateway.absoluteMediaURLs(req, []string{"/media/a.png"}); got[0] != "https://cdn.example/public/media/a.png" {
		t.Fatalf("configured base = %q", got[0])
	}
}

// The image endpoint has one model and no control over how many pictures come
// back, so both fields are accepted and ignored: honouring either could only
// mean rejecting the value the client guessed.
func TestImageGenerationsIgnoresTheModelAndCountFields(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamMedia("https://cdn/one.png")))
	h.addAccount(t, "primary", "token-good", 10)

	raw, _ := json.Marshal(map[string]any{"prompt": "a blue circle", "model": "dall-e-3", "n": 9})
	req := httptest.NewRequest(http.MethodPost, "/v1/images/generations", bytes.NewReader(raw))
	req.Header.Set("content-type", "application/json")
	req.Header.Set("authorization", "Bearer "+h.key)
	rec := httptest.NewRecorder()
	h.gateway.ImageGenerations(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	// One picture, because that is what the turn produced — not nine, because
	// that is what the caller asked for.
	if data, _ := decodeJSON(t, rec)["data"].([]any); len(data) != 1 {
		t.Fatalf("data = %v, want the 1 image the turn produced", decodeJSON(t, rec)["data"])
	}
}

// An inline data URI cannot be fetched by the upstream, so the request must be
// refused with an actionable message rather than forwarded to fail opaquely.
// An inline image is carried as bytes and pushed to the object store with the
// turn's own credential, exactly like an inline file. The upstream accepts an
// attachment only when it carries an object key owned by the turn's account (or
// a URL on its own allow-list), so a public base URL is not the mechanism and
// must not be required.
func TestChatCompletionsUploadsInlineImageWithoutPublicBaseURL(t *testing.T) {
	h, bodies, _ := newFileHarness(t)
	h.addAccount(t, "primary", "token-good", 10)

	rec := h.chat(t, map[string]any{
		"model": "minimax-agent",
		"messages": []any{map[string]any{
			"role": "user",
			"content": []any{
				map[string]any{"type": "text", "text": "what is this"},
				map[string]any{"type": "image_url", "image_url": map[string]any{
					"url": "data:image/png;base64,iVBORw0KGgo=",
				}},
			},
		}},
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body = %s", rec.Code, rec.Body.String())
	}
	if len(*bodies) != 1 {
		t.Fatalf("message bodies = %d, want 1", len(*bodies))
	}
	attachments, _ := (*bodies)[0]["attachments"].([]any)
	if len(attachments) != 1 {
		t.Fatalf("attachments = %#v, want one envelope", (*bodies)[0]["attachments"])
	}
	envelope, _ := attachments[0].(map[string]any)
	meta, _ := envelope["meta"].(map[string]any)
	cloud, _ := envelope["cloud"].(map[string]any)
	if meta == nil || meta["attachment_type"] != "image" {
		t.Errorf("meta = %v", meta)
	}
	if cloud == nil || cloud["object_key"] != "9484b529-a583-4f03-afe5-780ae74609c8" {
		t.Errorf("cloud = %v", cloud)
	}
}

// A remote image URL is forwarded as an attachment without being downloaded.
func TestChatCompletionsForwardsRemoteImage(t *testing.T) {
	var body string
	h := newHarness(t, upstreamFunc(
		nil,
		func(string) string { return upstreamText("看到了") },
	))
	// Wrap the stub to capture the message payload.
	original := h.upstream.Config.Handler
	h.upstream.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/message") {
			raw, _ := io.ReadAll(r.Body)
			body = string(raw)
		}
		original.ServeHTTP(w, r)
	})
	h.addAccount(t, "primary", "token-good", 10)

	rec := h.chat(t, map[string]any{
		"model": "minimax-agent",
		"messages": []any{map[string]any{
			"role": "user",
			"content": []any{
				map[string]any{"type": "text", "text": "what is this"},
				map[string]any{"type": "image_url", "image_url": map[string]any{
					"url": "https://example.com/cat.png",
				}},
			},
		}},
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if !strings.Contains(body, "https://example.com/cat.png") {
		t.Fatalf("message body should carry the remote image: %s", body)
	}
}

func accountID(t *testing.T, h *harness, name string) string {
	t.Helper()
	for _, account := range h.store.ListAccounts() {
		if account.Name == name {
			return account.ID
		}
	}
	t.Fatalf("account %q not found", name)
	return ""
}

// Guard against the harness drifting from the real configuration defaults.
func TestHarnessUsesFastCapacityWait(t *testing.T) {
	h := newHarness(t, func(w http.ResponseWriter, r *http.Request) {})
	if got := h.settings.CapacityWait(); got != 0 {
		t.Fatalf("capacity wait = %v, want 0 so pool-exhaustion tests stay fast", got)
	}
	if h.settings.RequestTimeout() > 5*time.Second {
		t.Fatalf("request timeout = %v, tests should not hang", h.settings.RequestTimeout())
	}
}

// --- video generation ------------------------------------------------------

// captureMessages records the `content` of every message turn sent upstream.
//
// The video integration lives entirely in that string — the plugin reference and
// the generation parameters have no request field of their own — so asserting on
// the request body is the only way to test it.
func (h *harness) captureMessages(t *testing.T) *[]string {
	t.Helper()
	var seen []string
	original := h.upstream.Config.Handler
	h.upstream.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/message") {
			raw, _ := io.ReadAll(r.Body)
			var payload struct {
				Content string `json:"content"`
			}
			if json.Unmarshal(raw, &payload) == nil {
				seen = append(seen, payload.Content)
			}
		}
		original.ServeHTTP(w, r)
	})
	return &seen
}

// A video model is not a chat model, but it is accepted on the chat surface:
// most clients only speak /v1/chat/completions, and refusing them there would
// make the feature unreachable for exactly the callers who need it.
func TestChatCompletionsRoutesAVideoModelThroughThePlugin(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("已提交")))
	h.addAccount(t, "primary", "token-good", 10)
	seen := h.captureMessages(t)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-h3-max",
		"messages": []any{map[string]any{"role": "user", "content": "一只猫在弹钢琴"}},
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if len(*seen) != 1 {
		t.Fatalf("expected one upstream turn, got %d", len(*seen))
	}
	text := (*seen)[0]

	if !strings.HasPrefix(text, "@video-creater ") {
		t.Errorf("turn does not open with the plugin reference:/n%s", text)
	}
	if !strings.Contains(text, "一只猫在弹钢琴") {
		t.Errorf("prompt was dropped:/n%s", text)
	}
	// The upstream reads the block only at the very end of the message.
	if !strings.HasSuffix(text, "</video-generation-options>") {
		t.Errorf("options block is not last:/n%s", text)
	}
	if !strings.Contains(text, `"model":"MiniMax-H3-Max"`) {
		t.Errorf("the selected model did not reach the options block:/n%s", text)
	}
}

// A chat model must not be rewritten: the plugin reference would send every
// ordinary conversation to a video plugin.
func TestChatCompletionsLeavesChatModelsAlone(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("你好")))
	h.addAccount(t, "primary", "token-good", 10)
	seen := h.captureMessages(t)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-agent",
		"messages": []any{map[string]any{"role": "user", "content": "你好"}},
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if len(*seen) != 1 || (*seen)[0] != "你好" {
		t.Fatalf("chat prompt was rewritten: %v", *seen)
	}
}

func TestVideoGenerationsReturnsMedia(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamMedia("https://cdn/clip.mp4")))
	h.addAccount(t, "primary", "token-good", 10)
	seen := h.captureMessages(t)

	raw, _ := json.Marshal(map[string]any{
		"model": "minimax-h3-max", "prompt": "一只猫在弹钢琴",
		"duration": 8, "ratio": "9:16", "resolution": "480P",
	})
	req := httptest.NewRequest(http.MethodPost, "/v1/videos/generations", bytes.NewReader(raw))
	req.Header.Set("content-type", "application/json")
	req.Header.Set("authorization", "Bearer "+h.key)
	rec := httptest.NewRecorder()
	h.gateway.VideoGenerations(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	payload := decodeJSON(t, rec)
	if payload["status"] != "succeeded" {
		t.Errorf("status = %v, want succeeded", payload["status"])
	}
	data, _ := payload["data"].([]any)
	if len(data) != 1 {
		t.Fatalf("data = %v, want one video", payload["data"])
	}
	first, _ := data[0].(map[string]any)
	if first["url"] != "https://cdn/clip.mp4" {
		t.Errorf("url = %v", first["url"])
	}

	// Every parameter the caller supplied has to survive into the options block.
	if len(*seen) != 1 {
		t.Fatalf("expected one upstream turn, got %d", len(*seen))
	}
	for _, want := range []string{`"duration":8`, `"ratio":"9:16"`, `"resolution":"480P"`, `"model":"MiniMax-H3-Max"`} {
		if !strings.Contains((*seen)[0], want) {
			t.Errorf("options block is missing %s:/n%s", want, (*seen)[0])
		}
	}
}

// Omitting a parameter must not stall the turn.
//
// The plugin's skill asks the user to confirm every unspecified choice before it
// generates, and a headless call has nobody to answer — so a gap in the options
// is not a harmless default, it is a request that never produces anything.
func TestVideoGenerationsFillsInEveryUnspecifiedParameter(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamMedia("https://cdn/clip.mp4")))
	h.addAccount(t, "primary", "token-good", 10)
	seen := h.captureMessages(t)

	raw, _ := json.Marshal(map[string]any{"prompt": "一只猫在弹钢琴"})
	req := httptest.NewRequest(http.MethodPost, "/v1/videos/generations", bytes.NewReader(raw))
	req.Header.Set("content-type", "application/json")
	req.Header.Set("authorization", "Bearer "+h.key)
	rec := httptest.NewRecorder()
	h.gateway.VideoGenerations(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if len(*seen) != 1 {
		t.Fatalf("expected one upstream turn, got %d", len(*seen))
	}
	text := (*seen)[0]
	// Defaults come from the settings, so read them rather than hardcoding.
	defaults := config.DefaultSettings(t.TempDir()).Video
	for _, want := range []string{
		`"duration":` + strconv.Itoa(defaults.DefaultDuration),
		`"ratio":"` + defaults.DefaultRatio + `"`,
		`"resolution":"` + defaults.DefaultResolution + `"`,
		`"model":"MiniMax-H3-Max"`, // the default video model
	} {
		if !strings.Contains(text, want) {
			t.Errorf("options block is missing %s:/n%s", want, text)
		}
	}
}

// A caller that gets no video back has to be able to tell "still running" from
// "failed" without reading prose, because the slow model legitimately returns
// nothing within any timeout.
func TestVideoGenerationsReportsPendingWhenNothingIsProduced(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("任务已提交，稍后生成完成")))
	h.addAccount(t, "primary", "token-good", 10)

	raw, _ := json.Marshal(map[string]any{"model": "minimax-h3", "prompt": "一只猫在弹钢琴"})
	req := httptest.NewRequest(http.MethodPost, "/v1/videos/generations", bytes.NewReader(raw))
	req.Header.Set("content-type", "application/json")
	req.Header.Set("authorization", "Bearer "+h.key)
	rec := httptest.NewRecorder()
	h.gateway.VideoGenerations(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("a slow generation is not an error: status = %d, body = %s", rec.Code, rec.Body.String())
	}
	payload := decodeJSON(t, rec)
	if payload["status"] != "pending" {
		t.Errorf("status = %v, want pending", payload["status"])
	}
	if detail, _ := payload["detail"].(string); !strings.Contains(detail, "任务已提交") {
		t.Errorf("the agent's own answer was dropped: %v", payload["detail"])
	}
}

// Naming a chat model on the video endpoint would send an ordinary conversation
// to a plugin, so it is refused rather than silently accepted.
func TestVideoGenerationsRejectsNonVideoModels(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("never reached")))
	h.addAccount(t, "primary", "token-good", 10)

	for _, model := range []string{"minimax-agent", "minimax-image", "does-not-exist"} {
		raw, _ := json.Marshal(map[string]any{"model": model, "prompt": "一只猫"})
		req := httptest.NewRequest(http.MethodPost, "/v1/videos/generations", bytes.NewReader(raw))
		req.Header.Set("content-type", "application/json")
		req.Header.Set("authorization", "Bearer "+h.key)
		rec := httptest.NewRecorder()
		h.gateway.VideoGenerations(rec, req)
		if rec.Code != http.StatusBadRequest {
			t.Errorf("model %q: status = %d, want 400", model, rec.Code)
		}
	}
}

func TestVideoGenerationsRequiresAPrompt(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("never reached")))
	h.addAccount(t, "primary", "token-good", 10)

	raw, _ := json.Marshal(map[string]any{"model": "minimax-h3-max"})
	req := httptest.NewRequest(http.MethodPost, "/v1/videos/generations", bytes.NewReader(raw))
	req.Header.Set("content-type", "application/json")
	req.Header.Set("authorization", "Bearer "+h.key)
	rec := httptest.NewRecorder()
	h.gateway.VideoGenerations(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", rec.Code)
	}
}

// captureMessageBodies records the whole body of every message turn.
//
// captureMessages keeps only `content`, which is enough for the prompt-shaped
// part of the integration. The parts that live in their own request fields —
// `client_intent` above all — are invisible to it.
func (h *harness) captureMessageBodies(t *testing.T) *[]map[string]any {
	t.Helper()
	var seen []map[string]any
	original := h.upstream.Config.Handler
	h.upstream.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/message") {
			raw, _ := io.ReadAll(r.Body)
			var payload map[string]any
			if json.Unmarshal(raw, &payload) == nil {
				seen = append(seen, payload)
			}
		}
		original.ServeHTTP(w, r)
	})
	return &seen
}

func (h *harness) video(t *testing.T, body map[string]any) *httptest.ResponseRecorder {
	t.Helper()
	raw, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/v1/videos/generations", bytes.NewReader(raw))
	req.Header.Set("content-type", "application/json")
	req.Header.Set("authorization", "Bearer "+h.key)
	rec := httptest.NewRecorder()
	h.gateway.VideoGenerations(rec, req)
	return rec
}

func (h *harness) image(t *testing.T, body map[string]any) *httptest.ResponseRecorder {
	t.Helper()
	raw, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/v1/images/generations", bytes.NewReader(raw))
	req.Header.Set("content-type", "application/json")
	req.Header.Set("authorization", "Bearer "+h.key)
	rec := httptest.NewRecorder()
	h.gateway.ImageGenerations(rec, req)
	return rec
}

// referenceURLs pulls the reference pictures out of a captured turn body.
//
// Files and images share the `attachments` array but not its shape, so the
// filter is on `type` rather than on position — a turn that carries both must
// not have its document counted as a picture.
func referenceURLs(t *testing.T, body map[string]any) []string {
	t.Helper()
	items, _ := body["attachments"].([]any)
	out := make([]string, 0, len(items))
	for _, item := range items {
		entry, _ := item.(map[string]any)
		if entry["type"] != "image" {
			continue
		}
		url, _ := entry["url"].(string)
		out = append(out, url)
	}
	return out
}

// A video turn has to carry `client_intent`, and a chat turn must not.
//
// This asserts *what is sent*, not what it achieves. The web client labels
// video turns and we follow it; whether the label is what unlocks the video
// service is still unproven — a live run that carried it produced no tool call
// and no file. Do not read this test as evidence that the field does anything
// beyond travelling.
func TestVideoTurnsCarryTheVideoClientIntent(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("已提交")))
	h.addAccount(t, "primary", "token-good", 10)
	bodies := h.captureMessageBodies(t)

	// The chat turn is sent first so its absence is asserted on the same run,
	// rather than assumed from a separate one.
	if rec := h.chat(t, map[string]any{
		"model":    "minimax-agent",
		"messages": []any{map[string]any{"role": "user", "content": "你好"}},
	}, h.key); rec.Code != http.StatusOK {
		t.Fatalf("chat status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if rec := h.video(t, map[string]any{"model": "minimax-h3-max", "prompt": "一只猫在弹钢琴"}); rec.Code != http.StatusOK {
		t.Fatalf("video status = %d, body = %s", rec.Code, rec.Body.String())
	}

	if len(*bodies) != 2 {
		t.Fatalf("captured %d turns, want 2", len(*bodies))
	}
	if got, present := (*bodies)[0]["client_intent"]; present {
		t.Errorf("an ordinary chat turn carried client_intent = %v", got)
	}
	if got := (*bodies)[1]["client_intent"]; got != minimax.VideoClientIntent {
		t.Errorf("video turn client_intent = %v, want %q", got, minimax.VideoClientIntent)
	}
}

// A finished video is not in the conversation stream, and a gateway that only
// reads the stream reports a success as emptiness.
//
// The file lands in the account's drive and is announced by a separate lookup:
// the turn's summaries carry its node id, and the drive's `download-url`
// sub-resource turns that node into a signed link. Both are plain GETs, so the
// cost of looking is one extra round trip on a turn that already cost points.
func TestVideoGenerationsFindsAFileThatOnlyTheDriveKnowsAbout(t *testing.T) {
	h := newHarness(t, func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/message"):
			w.Header().Set("content-type", "text/event-stream")
			_, _ = io.WriteString(w, upstreamText("已提交，正在生成"))
		case strings.HasSuffix(r.URL.Path, "/input-summaries"):
			w.Header().Set("content-type", "application/json")
			_, _ = io.WriteString(w, `{"summaries":[{"artifacts":[`+
				`{"category":"videos","file_ext":"mp4","mime_type":"video/mp4",`+
				`"name":"444160978935873.mp4","node_id":"444166461083748",`+
				`"size_bytes":801051,"created_at":9999999999999}]}],`+
				`"base_resp":{"status_code":0,"status_msg":"ok"}}`)
		case strings.HasSuffix(r.URL.Path, "/download-url"):
			w.Header().Set("content-type", "application/json")
			// The drive answers without a scheme; the gateway has to add one.
			_, _ = io.WriteString(w, `{"download_url":"matrix-internal.oss.example/Mavis/1/files/2/3.mp4?sig=x",`+
				`"base_resp":{"status_code":0,"status_msg":"ok"}}`)
		default:
			w.Header().Set("content-type", "application/json")
			_, _ = io.WriteString(w, `{"session_id":"sess_test"}`)
		}
	})
	h.addAccount(t, "primary", "token-good", 10)

	rec := h.video(t, map[string]any{"model": "minimax-h3-max", "prompt": "一只猫在弹钢琴"})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	payload := decodeJSON(t, rec)
	if payload["status"] != "succeeded" {
		t.Fatalf("status = %v, want succeeded (body %s)", payload["status"], rec.Body.String())
	}
	if _, present := payload["detail"]; present {
		t.Errorf("a succeeded turn still carried prose: %v", payload["detail"])
	}
	data, _ := payload["data"].([]any)
	if len(data) != 1 {
		t.Fatalf("data = %v, want one video", payload["data"])
	}
	first, _ := data[0].(map[string]any)
	if first["url"] != "https://matrix-internal.oss.example/Mavis/1/files/2/3.mp4?sig=x" {
		t.Errorf("url = %v, want the drive link with a scheme", first["url"])
	}
}

// A drive lookup that fails must not turn a good turn into an error, and must
// not be mistaken for "this turn produced nothing" either — the turn's own
// answer is what the caller gets.
func TestVideoGenerationsSurvivesADriveThatDoesNotAnswer(t *testing.T) {
	h := newHarness(t, func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/message"):
			w.Header().Set("content-type", "text/event-stream")
			_, _ = io.WriteString(w, upstreamText("已提交"))
		case strings.HasSuffix(r.URL.Path, "/input-summaries"):
			w.WriteHeader(http.StatusInternalServerError)
			_, _ = io.WriteString(w, "boom")
		default:
			w.Header().Set("content-type", "application/json")
			_, _ = io.WriteString(w, `{"session_id":"sess_test"}`)
		}
	})
	h.addAccount(t, "primary", "token-good", 10)

	rec := h.video(t, map[string]any{"model": "minimax-h3-max", "prompt": "一只猫在弹钢琴"})
	if rec.Code != http.StatusOK {
		t.Fatalf("a broken lookup is not the caller's error: status = %d, body = %s", rec.Code, rec.Body.String())
	}
	payload := decodeJSON(t, rec)
	if payload["status"] != "pending" {
		t.Errorf("status = %v, want pending", payload["status"])
	}
	if detail, _ := payload["detail"].(string); !strings.Contains(detail, "已提交") {
		t.Errorf("the agent's own answer was dropped: %v", payload["detail"])
	}
}

// ------------------------------------------------- reference video and audio

// Reference video and audio belong to the slow model alone.
//
// The fast variant's tool schema has no field for either, and a turn that
// carries one is not refused upstream — it produces nothing and still spends
// the account's credit. Refusing here is the only place the mistake is cheap.
func TestVideoGenerationsRefusesReferenceMediaOnTheFastModel(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("已提交")))
	h.addAccount(t, "primary", "token-good", 10)

	for _, field := range []string{"video_url", "audio_url"} {
		rec := h.video(t, map[string]any{
			"model": "minimax-h3-max", "prompt": "一只猫在弹钢琴",
			field: "data:video/mp4;base64,AAAA",
		})
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("%s: status = %d, want 400 (body %s)", field, rec.Code, rec.Body.String())
		}
		errObj, _ := decodeJSON(t, rec)["error"].(map[string]any)
		message, _ := errObj["message"].(string)
		if !strings.Contains(message, "MiniMax-H3") {
			t.Errorf("%s: the refusal has to name the model that would work: %v", field, message)
		}
	}
}

// A reference the gateway cannot turn into an owned file is refused with the
// reason, because the alternative is a turn that costs points and produces
// nothing.
func TestVideoGenerationsRefusesAReferenceMediaItCannotUpload(t *testing.T) {
	cases := []struct {
		name string
		body map[string]any
		want string
	}{
		{
			name: "a URL instead of bytes",
			body: map[string]any{"video_url": "https://example.test/clip.mp4"},
			want: "data: URI",
		},
		{
			name: "an audio field carrying a video",
			body: map[string]any{"audio_url": "data:video/mp4;base64,AAAA"},
			want: "audio/*",
		},
		{
			name: "a payload that is not base64",
			body: map[string]any{"video_url": "data:video/mp4;base64,!!!!"},
			want: "base64",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := newHarness(t, acceptsEverything(upstreamText("已提交")))
			h.addAccount(t, "primary", "token-good", 10)
			bodies := h.captureMessageBodies(t)

			body := map[string]any{"model": "minimax-h3", "prompt": "一只猫在弹钢琴"}
			for key, value := range tc.body {
				body[key] = value
			}
			rec := h.video(t, body)
			if rec.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want 400 (body %s)", rec.Code, rec.Body.String())
			}
			errObj, _ := decodeJSON(t, rec)["error"].(map[string]any)
			if message, _ := errObj["message"].(string); !strings.Contains(message, tc.want) {
				t.Errorf("message = %q, want it to mention %q", message, tc.want)
			}
			if len(*bodies) != 0 {
				t.Errorf("a refused request still spent %d upstream turn(s)", len(*bodies))
			}
		})
	}
}

// A turn that carries a reference video or audio is a different mode, and the
// mode lives in the text: the plugin owns the field names, but which attachment
// plays which role is the turn's to state.
func TestVideoGenerationsCarriesReferenceMediaOnTheSlowModel(t *testing.T) {
	h, bodies, _ := newFileHarness(t)
	h.addAccount(t, "primary", "token-good", 10)

	encode := func(payload string) string { return base64.StdEncoding.EncodeToString([]byte(payload)) }
	rec := h.video(t, map[string]any{
		"model": "minimax-h3", "prompt": "让画面跟着节奏动起来",
		"video_url": "data:video/mp4;base64," + encode("clip"),
		"audio_url": "data:audio/mpeg;base64," + encode("beat"),
	})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if len(*bodies) == 0 {
		t.Fatal("the reference turn never reached the upstream")
	}

	text, _ := (*bodies)[0]["content"].(string)
	for _, want := range []string{"多模态参考模式", "reference_videos", "reference_audios", "让画面跟着节奏动起来"} {
		if !strings.Contains(text, want) {
			t.Errorf("turn text is missing %q:\n%s", want, text)
		}
	}
	// The options block is anchored to the end of the message, so the sentence
	// that names the roles has to land before it rather than after.
	if index := strings.Index(text, "多模态参考模式"); index > strings.Index(text, "<video-generation-options>") {
		t.Errorf("the reference sentence landed after the options block:\n%s", text)
	}

	attachments, _ := (*bodies)[0]["attachments"].([]any)
	if len(attachments) != 2 {
		t.Fatalf("attachments = %#v, want the video and the audio", (*bodies)[0]["attachments"])
	}
	for index, want := range []string{"video/mp4", "audio/mpeg"} {
		entry, _ := attachments[index].(map[string]any)
		meta, _ := entry["meta"].(map[string]any)
		cloud, _ := entry["cloud"].(map[string]any)
		if meta == nil || cloud == nil {
			t.Fatalf("attachment %d = %#v, want meta and cloud", index, entry)
		}
		if meta["attachment_type"] != "file" || meta["mime_type"] != want {
			t.Errorf("attachment %d meta = %v, want a %s file", index, meta, want)
		}
		if cloud["object_key"] == "" {
			t.Errorf("attachment %d never became an owned object key: %v", index, cloud)
		}
	}
}

// A reference-only turn that carries pictures has to say it is a reference set:
// without the sentence the same pictures are read as a first frame, and the
// multimodal mode is the one that forbids a first frame.
func TestVideoGenerationsSendsPicturesAsReferencesWhenAsked(t *testing.T) {
	h, bodies, _ := newFileHarness(t)
	h.addAccount(t, "primary", "token-good", 10)

	const inline = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
	rec := h.video(t, map[string]any{
		"model": "minimax-h3", "prompt": "让画面动起来",
		"image_url": inline, "reference_mode": true,
	})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	text, _ := (*bodies)[0]["content"].(string)
	for _, want := range []string{"reference_images", "input_image", "让画面动起来"} {
		if !strings.Contains(text, want) {
			t.Errorf("turn text is missing %q:\n%s", want, text)
		}
	}
}

// Asking for the mode with nothing to put in it is a mistake worth naming, not
// a turn that spends an account to produce nothing.
func TestVideoGenerationsRefusesAReferenceModeWithNothingAttached(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("已提交")))
	h.addAccount(t, "primary", "token-good", 10)
	bodies := h.captureMessageBodies(t)

	rec := h.video(t, map[string]any{
		"model": "minimax-h3", "prompt": "让画面动起来", "reference_mode": true,
	})
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400 (body %s)", rec.Code, rec.Body.String())
	}
	if len(*bodies) != 0 {
		t.Errorf("a refused request still spent %d upstream turn(s)", len(*bodies))
	}
}

// An account that cannot pay is a fact about the account, and the pool is what
// fixes it — so the turn is retried elsewhere rather than handed back as "the
// model produced nothing".
func TestVideoGenerationsTriesAnotherAccountWhenOneCannotPay(t *testing.T) {
	h := newHarness(t, upstreamFunc(nil, func(token string) string {
		if token == "token-broke" {
			return upstreamText("提交被拒：insufficient account credits for MiniMax-H3 video generation; " +
				"this task needs 250 credits, and earlier generations still owe 3550 credits")
		}
		return upstreamMedia("https://cdn/clip.mp4")
	}))
	broke := h.addAccount(t, "broke", "token-broke", 10)
	h.addAccount(t, "rich", "token-rich", 10)

	rec := h.video(t, map[string]any{"model": "minimax-h3-max", "prompt": "一只猫在弹钢琴"})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	payload := decodeJSON(t, rec)
	if payload["status"] != "succeeded" {
		t.Fatalf("status = %v, want succeeded — the paying account has the file (%s)", payload["status"], rec.Body.String())
	}
	if data, _ := payload["data"].([]any); len(data) != 1 {
		t.Errorf("data = %v, want the file from the second account", payload["data"])
	}
	// The account that could not pay is taken out of rotation, so the next
	// caller does not have to rediscover it.
	after, ok := h.store.AccountByID(broke.ID)
	if !ok || after.FailCount == 0 || after.Status != store.StatusCooldown {
		t.Errorf("the account that could not pay was not set aside: %+v", after)
	}
}

// When every account tried is refused for lack of credit, saying so beats
// handing back an empty success that reads as "nothing was produced".
func TestVideoGenerationsSaysSoWhenNoAccountCanPay(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("insufficient account credits for MiniMax-H3 video generation")))
	h.addAccount(t, "one", "token-one", 10)
	h.addAccount(t, "two", "token-two", 10)
	h.addAccount(t, "three", "token-three", 10)

	rec := h.video(t, map[string]any{"model": "minimax-h3-max", "prompt": "一只猫在弹钢琴"})
	if rec.Code != http.StatusPaymentRequired {
		t.Fatalf("status = %d, want 402 (body %s)", rec.Code, rec.Body.String())
	}
}

// An empty fast turn is retried even when it does not name the balance: the
// other ways it comes back empty — a connector that is not there, a runtime
// that stopped mid-sentence — belong to the account too, and the caller has
// nothing either way.
func TestVideoGenerationsRetriesAnEmptyFastTurn(t *testing.T) {
	var calls int32
	h := newHarness(t, func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/message") {
			w.Header().Set("content-type", "text/event-stream")
			if atomic.AddInt32(&calls, 1) == 1 {
				_, _ = io.WriteString(w, upstreamText("我先看看这个请求该怎么处理。"))
				return
			}
			_, _ = io.WriteString(w, upstreamMedia("https://cdn/clip.mp4"))
			return
		}
		w.Header().Set("content-type", "application/json")
		_, _ = io.WriteString(w, `{"session_id":"sess_test"}`)
	})
	h.addAccount(t, "primary", "token-good", 10)
	h.addAccount(t, "spare", "token-spare", 10)

	rec := h.video(t, map[string]any{"model": "minimax-h3-max", "prompt": "一只猫在弹钢琴"})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if payload := decodeJSON(t, rec); payload["status"] != "succeeded" {
		t.Errorf("status = %v, want succeeded on the retry", payload["status"])
	}
	if got := atomic.LoadInt32(&calls); got != 2 {
		t.Errorf("upstream turns = %d, want 2", got)
	}
}

// The slow variant's submitting turn is supposed to be empty. Retrying it would
// submit the same generation to three accounts and pay for all three.
func TestVideoGenerationsDoesNotRetryAnEmptySlowTurn(t *testing.T) {
	var turns int32
	h := newHarness(t, func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/message") {
			atomic.AddInt32(&turns, 1)
			w.Header().Set("content-type", "text/event-stream")
			_, _ = io.WriteString(w, upstreamText("任务已提交，等生成结果。"))
			return
		}
		w.Header().Set("content-type", "application/json")
		_, _ = io.WriteString(w, `{"session_id":"sess_test"}`)
	})
	account := h.addAccount(t, "primary", "token-good", 10)

	rec := h.video(t, map[string]any{"model": "minimax-h3", "prompt": "一只猫在弹钢琴"})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if payload := decodeJSON(t, rec); payload["status"] != "pending" {
		t.Errorf("status = %v, want pending", payload["status"])
	}
	if got := atomic.LoadInt32(&turns); got != 1 {
		t.Errorf("upstream turns = %d, want 1 (a slow submit is empty by design)", got)
	}
	after, _ := h.store.AccountByID(account.ID)
	if after.FailCount != 0 || after.Status != store.StatusActive {
		t.Errorf("an empty slow turn was charged to the account: %+v", after)
	}
}

// The agent says this in its own words as often as it quotes the upstream, and
// the quote is not what reaches the gateway — the stream carries message frames
// only. The check only ever picks a message, so it only has to cover the
// phrasings seen live.
func TestOutOfCreditIsRecognisedInEitherLanguage(t *testing.T) {
	cases := map[string]bool{
		"提交被拒：insufficient account credits for MiniMax-H3 video generation": true,
		"搞砸了——账号积分不够。这条视频没法生成。":                                             true,
		"这条视频没生成出来，停在账户余额这一关。":                                              true,
		"这次账户余额不够，要 250 积分但还有 3550 的待结算费用挡着":                                true,
		"我先看看这个请求该怎么处理。":                                                    false,
		"已提交，正在生成":                                                          false,
	}
	for text, want := range cases {
		if got := isOutOfCredit(text); got != want {
			t.Errorf("isOutOfCredit(%.30q) = %v, want %v", text, got, want)
		}
	}
}

// --------------------------------------------------- the second half of a turn

func (h *harness) sessionMedia(t *testing.T, session, account string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/v1/videos/sessions/"+session+"?account="+account, nil)
	req.Header.Set("authorization", "Bearer "+h.key)
	req.SetPathValue("session", session)
	rec := httptest.NewRecorder()
	h.gateway.VideoSessionMedia(rec, req)
	return rec
}

// The slow model's file lands minutes after the turn ends, so the turn cannot
// be the only place it is looked for.
func TestVideoSessionMediaFindsAFileThatLandedAfterTheTurn(t *testing.T) {
	// The drive signs a fresh URL on every request, which is why identity has
	// to come from the path: two calls, two signatures, one file.
	var signatures int32
	h := newHarness(t, func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/input-summaries"):
			w.Header().Set("content-type", "application/json")
			_, _ = io.WriteString(w, `{"summaries":[{"artifacts":[`+
				`{"category":"videos","file_ext":"mp4","mime_type":"video/mp4",`+
				`"name":"444160978935873.mp4","node_id":"444166461083748",`+
				`"size_bytes":801051,"created_at":1}]}],`+
				`"base_resp":{"status_code":0,"status_msg":"ok"}}`)
		case strings.HasSuffix(r.URL.Path, "/download-url"):
			w.Header().Set("content-type", "application/json")
			fmt.Fprintf(w, `{"download_url":"matrix-internal.oss.example/Mavis/1/files/2/3.mp4?sig=%d",`+
				`"base_resp":{"status_code":0,"status_msg":"ok"}}`, atomic.AddInt32(&signatures, 1))
		default:
			w.Header().Set("content-type", "application/json")
			_, _ = io.WriteString(w, `{"session_id":"sess_test"}`)
		}
	})
	account := h.addAccount(t, "primary", "token-good", 10)

	rec := h.sessionMedia(t, "sess_late", account.ID)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	payload := decodeJSON(t, rec)
	if payload["status"] != "succeeded" {
		t.Fatalf("status = %v, want succeeded (%s)", payload["status"], rec.Body.String())
	}
	data, _ := payload["data"].([]any)
	if len(data) != 1 {
		t.Fatalf("data = %v, want the one video", payload["data"])
	}
	first, _ := data[0].(map[string]any)
	stored, _ := first["url"].(string)
	if !strings.Contains(stored, "/files/2/3.mp4") {
		t.Errorf("url = %v, want the file the drive pointed at", first["url"])
	}

	// Polling is the normal way to use this, so the second call must not store
	// the same file again — and must not hand back a second copy of it.
	second := decodeJSON(t, h.sessionMedia(t, "sess_late", account.ID))
	again, _ := second["data"].([]any)
	if len(again) != 1 {
		t.Fatalf("second call = %v, want the same single video", again)
	}
	if entry, _ := again[0].(map[string]any); entry["url"] != stored {
		t.Errorf("second call = %v, want the same copy %q", entry["url"], stored)
	}
	if media := h.store.ListMedia(); len(media) != 1 {
		t.Errorf("media = %d, want 1 (a poll is not a new file)", len(media))
	}
}

// A turn that produced nothing yet answers `pending` rather than an error: the
// session is real and the file may still be coming.
func TestVideoSessionMediaReportsPendingForASessionWithNoFiles(t *testing.T) {
	h := newHarness(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("content-type", "application/json")
		if strings.HasSuffix(r.URL.Path, "/input-summaries") {
			_, _ = io.WriteString(w, `{"summaries":[],"base_resp":{"status_code":0,"status_msg":"ok"}}`)
			return
		}
		_, _ = io.WriteString(w, `{"session_id":"sess_test"}`)
	})
	account := h.addAccount(t, "primary", "token-good", 10)

	rec := h.sessionMedia(t, "sess_empty", account.ID)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	payload := decodeJSON(t, rec)
	if payload["status"] != "pending" {
		t.Errorf("status = %v, want pending", payload["status"])
	}
	if data, _ := payload["data"].([]any); len(data) != 0 {
		t.Errorf("data = %v, want nothing", data)
	}
}

// The account has to be one this gateway knows, and the key has to be a real
// one: the lookup runs with that account's own credentials, so an unchecked id
// would be a way to point the gateway at anything.
func TestVideoSessionMediaRefusesWhatItCannotLookUp(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("已提交")))
	account := h.addAccount(t, "primary", "token-good", 10)

	if rec := h.sessionMedia(t, "sess_late", "acc_nope"); rec.Code != http.StatusBadRequest {
		t.Errorf("unknown account: status = %d, want 400", rec.Code)
	}

	req := httptest.NewRequest(http.MethodGet, "/v1/videos/sessions/sess_late?account="+account.ID, nil)
	req.Header.Set("authorization", "Bearer sk-not-a-key")
	req.SetPathValue("session", "sess_late")
	rec := httptest.NewRecorder()
	h.gateway.VideoSessionMedia(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Errorf("bad key: status = %d, want 401", rec.Code)
	}
}

// ------------------------------------------------------------------ ranges

// videoMatrix is every combination the two H3 variants advertise, as the model
// picker shows them.
var videoMatrix = []struct {
	id          string
	upstream    string
	resolutions []string
}{
	{"minimax-h3-max", "MiniMax-H3-Max", []string{"480P", "768P"}},
	{"minimax-h3", "MiniMax-H3", []string{"768P", "2K"}},
}

var videoMatrixRatios = []string{"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}

// The whole advertised matrix, driven through the endpoint.
//
// This is 264 turns - 11 durations by 6 ratios by 2 resolutions, for each of
// the two models - and it is worth all of them, because "supported" is a claim
// and the endpoint is where a caller finds out it was wrong. A rejection here
// is what turns into a support ticket, and it would look like a broken gateway
// rather than a bad parameter: the caller asked for something the model's own
// panel offers.
func TestVideoGenerationsAcceptsEveryAdvertisedCombination(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamMedia("https://cdn/clip.mp4")))
	h.addAccount(t, "primary", "token-good", 10)
	seen := h.captureMessages(t)

	turns := 0
	for _, model := range videoMatrix {
		for _, ratio := range videoMatrixRatios {
			for _, resolution := range model.resolutions {
				for duration := 5; duration <= 15; duration++ {
					turns++
					rec := h.video(t, map[string]any{
						"model": model.id, "prompt": "一只猫在弹钢琴",
						"ratio": ratio, "resolution": resolution, "duration": duration,
					})
					if rec.Code != http.StatusOK {
						t.Fatalf("%s %s %s %ds: status = %d, body = %s",
							model.id, ratio, resolution, duration, rec.Code, rec.Body.String())
					}

					payload := decodeJSON(t, rec)
					if adjusted, ok := payload["adjusted"]; ok {
						t.Errorf("%s %s %s %ds was repaired: %v",
							model.id, ratio, resolution, duration, adjusted)
					}
					params, _ := payload["params"].(map[string]any)
					if params == nil {
						t.Fatalf("%s %s %s %ds: the response carries no params",
							model.id, ratio, resolution, duration)
					}
					for field, want := range map[string]any{
						"model": model.upstream, "ratio": ratio,
						"resolution": resolution, "duration": float64(duration),
					} {
						if params[field] != want {
							t.Errorf("%s %s %s %ds: params[%s] = %v, want %v",
								model.id, ratio, resolution, duration, field, params[field], want)
						}
					}

					// The response is the gateway's account of itself; the turn
					// text is what upstream actually receives. Both have to say
					// the same thing.
					if len(*seen) != turns {
						t.Fatalf("expected %d upstream turns, got %d", turns, len(*seen))
					}
					text := (*seen)[turns-1]
					for _, want := range []string{
						`"duration":` + strconv.Itoa(duration),
						`"ratio":"` + ratio + `"`,
						`"resolution":"` + resolution + `"`,
						`"model":"` + model.upstream + `"`,
					} {
						if !strings.Contains(text, want) {
							t.Errorf("%s %s %s %ds: options block is missing %s:/n%s",
								model.id, ratio, resolution, duration, want, text)
						}
					}
				}
			}
		}
	}

	if want := len(videoMatrix) * len(videoMatrixRatios) * 2 * 11; turns != want {
		t.Errorf("covered %d combinations, want %d", turns, want)
	}
}

// A value outside the model's range is repaired, reported, and never reaches
// upstream as asked.
//
// All three matter. Rejecting it would fail a request over a parameter the
// caller cannot look up anywhere in this API; repairing it silently would hand
// back a video that is not the one that was ordered, at a price the caller did
// not agree to; and repairing it without repairing the outgoing text would mean
// the response and the turn disagree.
func TestVideoGenerationsRepairsAValueTheModelDoesNotOffer(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamMedia("https://cdn/clip.mp4")))
	h.addAccount(t, "primary", "token-good", 10)
	seen := h.captureMessages(t)

	// 2K and 30s are both outside what H3-Max offers; 16:9 is inside it and has
	// to be left alone.
	rec := h.video(t, map[string]any{
		"model": "minimax-h3-max", "prompt": "一只猫在弹钢琴",
		"ratio": "16:9", "resolution": "2K", "duration": 30,
	})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}

	payload := decodeJSON(t, rec)
	params, _ := payload["params"].(map[string]any)
	if params["resolution"] != "768P" {
		t.Errorf("resolution = %v, want the nearest offered value 768P", params["resolution"])
	}
	if params["duration"] != float64(15) {
		t.Errorf("duration = %v, want the nearest offered value 15", params["duration"])
	}
	if params["ratio"] != "16:9" {
		t.Errorf("an accepted ratio was changed: %v", params["ratio"])
	}

	adjusted, _ := payload["adjusted"].([]any)
	if len(adjusted) != 2 {
		t.Fatalf("adjusted = %v, want the two repaired fields", payload["adjusted"])
	}
	reported := map[string][2]string{}
	for _, item := range adjusted {
		entry, _ := item.(map[string]any)
		field, _ := entry["field"].(string)
		requested, _ := entry["requested"].(string)
		used, _ := entry["used"].(string)
		reported[field] = [2]string{requested, used}
	}
	if reported["resolution"] != [2]string{"2K", "768P"} {
		t.Errorf("resolution adjustment = %v", reported["resolution"])
	}
	if reported["duration"] != [2]string{"30", "15"} {
		t.Errorf("duration adjustment = %v", reported["duration"])
	}

	if len(*seen) != 1 {
		t.Fatalf("expected one upstream turn, got %d", len(*seen))
	}
	text := (*seen)[0]
	if strings.Contains(text, `"resolution":"2K"`) {
		t.Errorf("the unsupported resolution reached upstream:/n%s", text)
	}
	if strings.Contains(text, `"duration":30`) {
		t.Errorf("the unsupported duration reached upstream:/n%s", text)
	}
	for _, want := range []string{`"resolution":"768P"`, `"duration":15`} {
		if !strings.Contains(text, want) {
			t.Errorf("the repaired value is missing %s:/n%s", want, text)
		}
	}
}

// The two models disagree about resolution, so the same request is accepted on
// one and repaired on the other. That asymmetry is the reason the ranges live
// on the model rather than in the settings: a global default cannot be right
// for both.
func TestVideoGenerationsRepairsDifferentlyPerModel(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamMedia("https://cdn/clip.mp4")))
	h.addAccount(t, "primary", "token-good", 10)
	h.captureMessages(t)

	// 480P is H3-Max's floor and below H3.0's: accepted by one, moved by the
	// other.
	accepted := decodeJSON(t, h.video(t, map[string]any{
		"model": "minimax-h3-max", "prompt": "一只猫在弹钢琴",
		"ratio": "16:9", "resolution": "480P", "duration": 5,
	}))
	if _, ok := accepted["adjusted"]; ok {
		t.Errorf("H3-Max repaired 480P, which it offers: %v", accepted["adjusted"])
	}

	repaired := decodeJSON(t, h.video(t, map[string]any{
		"model": "minimax-h3", "prompt": "一只猫在弹钢琴",
		"ratio": "16:9", "resolution": "480P", "duration": 5,
	}))
	params, _ := repaired["params"].(map[string]any)
	if params["resolution"] != "768P" {
		t.Errorf("H3.0 resolution = %v, want 768P", params["resolution"])
	}
	if _, ok := repaired["adjusted"]; !ok {
		t.Error("H3.0 repaired 480P without saying so")
	}
}

// Hailuo's panel has not been read, so nothing about it is enforced.
//
// The endpoint must not turn that gap into a rejection: a range invented from
// the H3 variants would fail requests this model may well accept, and the
// caller has no way to tell that from a broken gateway.
func TestVideoGenerationsLeavesAnUnreadModelUnconstrained(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamMedia("https://cdn/clip.mp4")))
	h.addAccount(t, "primary", "token-good", 10)
	seen := h.captureMessages(t)

	rec := h.video(t, map[string]any{
		"model": "minimax-hailuo-2-3", "prompt": "一只猫在弹钢琴",
		"ratio": "5:4", "resolution": "1080P", "duration": 25,
	})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	payload := decodeJSON(t, rec)
	if _, ok := payload["adjusted"]; ok {
		t.Errorf("an unread model was repaired: %v", payload["adjusted"])
	}
	if len(*seen) != 1 {
		t.Fatalf("expected one upstream turn, got %d", len(*seen))
	}
	for _, want := range []string{`"ratio":"5:4"`, `"resolution":"1080P"`, `"duration":25`} {
		if !strings.Contains((*seen)[0], want) {
			t.Errorf("options block is missing %s:/n%s", want, (*seen)[0])
		}
	}
}

// The chat surface builds the same options block and has no field to report a
// repair in, so the check that matters there is that the repair happened at all.
func TestChatCompletionsRepairsAVideoTurnToo(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("已提交")))
	h.addAccount(t, "primary", "token-good", 10)
	seen := h.captureMessages(t)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-h3",
		"messages": []any{map[string]any{"role": "user", "content": "一只猫在弹钢琴"}},
	}, h.key)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if len(*seen) != 1 {
		t.Fatalf("expected one upstream turn, got %d", len(*seen))
	}
	text := (*seen)[0]
	// The console default is 768P, which H3.0 offers, so this is the settings
	// path rather than a repair — but it still has to come out as a complete,
	// accepted block.
	for _, want := range []string{
		`"model":"MiniMax-H3"`, `"ratio":"16:9"`, `"resolution":"768P"`, `"duration":5`,
	} {
		if !strings.Contains(text, want) {
			t.Errorf("options block is missing %s:/n%s", want, text)
		}
	}
}

// --------------------------------------------------------------- references

// A reference picture turns an image turn into an edit, and it travels as an
// attachment.
//
// Until now this endpoint dropped them on the floor: the handler passed a
// literal nil to the turn, so a caller could send a reference and get back a
// picture that ignored it, with nothing anywhere saying so.
func TestImageGenerationsCarriesReferenceImages(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamMedia("https://cdn/out.png")))
	h.addAccount(t, "primary", "token-good", 10)
	bodies := h.captureMessageBodies(t)

	rec := h.image(t, map[string]any{
		"prompt":    "把这张图改成水彩风格",
		"image_url": "https://example.test/cat.png",
	})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if len(*bodies) != 1 {
		t.Fatalf("expected one upstream turn, got %d", len(*bodies))
	}
	got := referenceURLs(t, (*bodies)[0])
	if len(got) != 1 || got[0] != "https://example.test/cat.png" {
		t.Fatalf("references = %v, want the one that was sent", got)
	}
	if _, adjusted := decodeJSON(t, rec)["adjusted"]; adjusted {
		t.Error("an accepted reference set was reported as adjusted")
	}
}

// The three spellings are additive, in a fixed order.
//
// `image` is the field OpenAI's image-edit surface uses and `image_url` is what
// this gateway's video endpoint has always taken; a client will send whichever
// it already has wired up. Dropping one because it arrived under the other name
// would lose a picture the caller can see in their own request.
func TestImageGenerationsAcceptsEveryReferenceSpelling(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamMedia("https://cdn/out.png")))
	h.addAccount(t, "primary", "token-good", 10)
	bodies := h.captureMessageBodies(t)

	rec := h.image(t, map[string]any{
		"prompt":     "把这四张合成一张",
		"image":      "https://example.test/a.png",
		"image_url":  "https://example.test/b.png",
		"image_urls": []any{"https://example.test/c.png", "https://example.test/d.png"},
	})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	want := []string{
		"https://example.test/a.png",
		"https://example.test/b.png",
		"https://example.test/c.png",
		"https://example.test/d.png",
	}
	got := referenceURLs(t, (*bodies)[0])
	if len(got) != len(want) {
		t.Fatalf("references = %v, want %v", got, want)
	}
	for index := range want {
		if got[index] != want[index] {
			t.Fatalf("references = %v, want %v (order matters: it is how a caller says which frame is first)", got, want)
		}
	}
}

// The cap is a request-size guard, not a plugin limit: H3-Max takes a reference
// set the way H3.0 does, and was measured taking sixteen in one turn. When it
// does bite, the first N are the ones kept — the order the caller sends them in
// is how they say which picture is the first frame.
func TestVideoGenerationsCapsReferencesAtWhatTheModelAccepts(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamMedia("https://cdn/clip.mp4")))
	h.addAccount(t, "primary", "token-good", 10)
	bodies := h.captureMessageBodies(t)

	sent := make([]any, 0, 9)
	for i := 0; i < 9; i++ {
		sent = append(sent, fmt.Sprintf("https://example.test/%02d.png", i))
	}
	rec := h.video(t, map[string]any{
		"model": "minimax-h3-max", "prompt": "一只猫在弹钢琴",
		"image_urls": sent,
	})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}

	payload := decodeJSON(t, rec)
	adjusted, _ := payload["adjusted"].([]any)
	if len(adjusted) != 1 {
		t.Fatalf("adjusted = %v, want one entry for the trimmed set", payload["adjusted"])
	}
	entry, _ := adjusted[0].(map[string]any)
	if entry["field"] != "reference_images" || entry["requested"] != "9" || entry["used"] != "8" {
		t.Errorf("adjustment = %v", entry)
	}

	got := referenceURLs(t, (*bodies)[0])
	if len(got) != 8 {
		t.Fatalf("references sent = %v, want eight", got)
	}
	if got[0] != "https://example.test/00.png" || got[7] != "https://example.test/07.png" {
		t.Errorf("references sent = %v, want the first eight in order", got)
	}
}

// H3.0 takes a reference set rather than a frame pair, so nothing is trimmed.
// The cap is per model for the same reason the resolutions are.
func TestVideoGenerationsKeepsEveryReferenceH3Accepts(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamMedia("https://cdn/clip.mp4")))
	h.addAccount(t, "primary", "token-good", 10)
	bodies := h.captureMessageBodies(t)

	rec := h.video(t, map[string]any{
		"model": "minimax-h3", "prompt": "一只猫在弹钢琴",
		"image_urls": []any{
			"https://example.test/1.png",
			"https://example.test/2.png",
			"https://example.test/3.png",
			"https://example.test/4.png",
		},
	})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if _, adjusted := decodeJSON(t, rec)["adjusted"]; adjusted {
		t.Error("H3.0 reported a trim it does not have")
	}
	if got := referenceURLs(t, (*bodies)[0]); len(got) != 4 {
		t.Errorf("references sent = %v, want all four", got)
	}
}

// The chat surface carries references as message content rather than as request
// fields, and the cap has to apply there too — otherwise the same picture set
// is trimmed on one endpoint and not on the other.
func TestChatCompletionsCapsReferencesOnAVideoModel(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("已提交")))
	h.addAccount(t, "primary", "token-good", 10)
	bodies := h.captureMessageBodies(t)

	content := []any{map[string]any{"type": "text", "text": "一只猫在弹钢琴"}}
	for i := 0; i < 9; i++ {
		content = append(content, map[string]any{
			"type": "image_url", "image_url": map[string]any{"url": fmt.Sprintf("https://example.test/%d.png", i)},
		})
	}
	rec := h.chat(t, map[string]any{
		"model":    "minimax-h3-max",
		"messages": []any{map[string]any{"role": "user", "content": content}},
	}, h.key)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if got := referenceURLs(t, (*bodies)[0]); len(got) != 8 {
		t.Errorf("references sent = %v, want eight", got)
	}
}

// A reference the agent cannot fetch is refused, and the refusal says what
// would work.
//
// A bare base64 payload is the mistake a client makes when it assumes these
// endpoints follow the image-edit convention. Forwarding it produces an
// upstream failure that names nothing and spends a turn; and because the value
// is a whole picture, it must not be echoed into a message that lands in the
// audit log.
func TestGenerationRefusesAReferenceItCannotFetch(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamMedia("https://cdn/out.png")))
	h.addAccount(t, "primary", "token-good", 10)
	bodies := h.captureMessageBodies(t)

	// One transparent pixel, as a client would send it.
	const bare = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

	cases := []struct {
		name string
		call func(*testing.T, map[string]any) *httptest.ResponseRecorder
	}{
		{"images", h.image},
		{"videos", h.video},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rec := tc.call(t, map[string]any{"prompt": "一只猫", "image_url": bare})
			if rec.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
			}
			message := rec.Body.String()
			if !strings.Contains(message, "http(s) URL") {
				t.Errorf("the refusal does not say what would work: %s", message)
			}
			if strings.Contains(message, bare) {
				t.Errorf("the rejected payload was echoed back into a logged message: %s", message)
			}
		})
	}

	if len(*bodies) != 0 {
		t.Errorf("a refused request still spent %d upstream turn(s)", len(*bodies))
	}
}

// A data URI reference is accepted with no public base URL configured: the
// bytes are pushed through the private upload inside the turn, which is the
// only attachment shape the upstream takes from us.
func TestGenerationAcceptsAnInlineReferenceWithoutAPublicBaseURL(t *testing.T) {
	h, bodies, _ := newFileHarness(t)
	h.addAccount(t, "primary", "token-good", 10)

	const inline = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

	rec := h.image(t, map[string]any{"prompt": "改成水彩", "image_url": inline})
	if rec.Code == http.StatusBadRequest && strings.Contains(rec.Body.String(), "public base URL") {
		t.Fatalf("an inline reference must not be refused for a missing public base URL: %s", rec.Body.String())
	}
	if len(*bodies) == 0 {
		t.Fatalf("the reference turn never reached the upstream (status %d: %s)", rec.Code, rec.Body.String())
	}
	attachments, _ := (*bodies)[0]["attachments"].([]any)
	if len(attachments) != 1 {
		t.Fatalf("attachments = %#v, want one envelope", (*bodies)[0]["attachments"])
	}
	cloud, _ := attachments[0].(map[string]any)["cloud"].(map[string]any)
	if cloud == nil || cloud["object_key"] == "" {
		t.Errorf("the reference did not become an owned object key: %#v", attachments[0])
	}
}

// --- credit failover -------------------------------------------------------

// A spent account does not fail: it answers, and the answer is a refusal.
// There is no error to fail over on, so the retry has to be driven by the
// text — and the account has to be parked, or the next request draws the same
// answer again.
func TestChatCompletionsRetriesACreditRefusal(t *testing.T) {
	var attempts []string
	h := newHarness(t, upstreamFunc(
		nil,
		func(token string) string {
			attempts = append(attempts, token)
			if token == "token-spent" {
				return upstreamText("抱歉，当前账号积分不足，无法继续执行这次请求。")
			}
			return upstreamText("换号成功")
		},
	))

	spent := h.addAccount(t, "spent", "token-spent", 1)
	h.addAccount(t, "funded", "token-good", 50)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-agent",
		"messages": []any{map[string]any{"role": "user", "content": "hi"}},
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 after failover; body = %s", rec.Code, rec.Body.String())
	}
	choices, _ := decodeJSON(t, rec)["choices"].([]any)
	message, _ := choices[0].(map[string]any)["message"].(map[string]any)
	if message["content"] != "换号成功" {
		t.Fatalf("content = %v, want the funded account's answer", message["content"])
	}
	if len(attempts) != 2 || attempts[0] != "token-spent" || attempts[1] != "token-good" {
		t.Fatalf("attempts = %v, want the spent account first and one retry", attempts)
	}

	updated, ok := h.store.AccountByID(spent.ID)
	if !ok {
		t.Fatal("account disappeared")
	}
	if updated.Status != store.StatusCooldown {
		t.Fatalf("status = %q, want the spent account parked", updated.Status)
	}
	if updated.Credit.Total != 0 {
		t.Fatalf("credit = %d, want the balance recorded as spent", updated.Credit.Total)
	}
}

// The streaming path is the one that would otherwise be unable to retry: the
// refusal is streamed before the turn ends. The opening of the answer is held
// back for exactly this, so the caller sees one clean answer from the second
// account and never the refusal.
func TestChatCompletionsStreamsPastACreditRefusal(t *testing.T) {
	h := newHarness(t, upstreamFunc(
		nil,
		func(token string) string {
			if token == "token-spent" {
				return upstreamText("抱歉，当前账号积分不足，无法继续。")
			}
			return upstreamText("换号成功")
		},
	))
	h.addAccount(t, "spent", "token-spent", 1)
	h.addAccount(t, "funded", "token-good", 50)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-agent",
		"messages": []any{map[string]any{"role": "user", "content": "hi"}},
		"stream":   true,
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	body := rec.Body.String()
	if strings.Contains(body, "积分不足") {
		t.Fatalf("the refusal reached the client: %s", body)
	}
	if !strings.Contains(body, "换号成功") {
		t.Fatalf("the retried answer never arrived: %s", body)
	}
}

// When every account is spent there is nothing left to try, and the refusal is
// more useful than a bare error: it says what is wrong in the provider's own
// words.
func TestChatCompletionsReturnsTheRefusalWhenEveryAccountIsSpent(t *testing.T) {
	const refusal = "抱歉，账户余额不足，无法完成这次请求。"
	h := newHarness(t, acceptsEverything(upstreamText(refusal)))
	h.addAccount(t, "a", "token-a", 1)
	h.addAccount(t, "b", "token-b", 2)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-agent",
		"messages": []any{map[string]any{"role": "user", "content": "hi"}},
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 with the refusal as the answer; body = %s", rec.Code, rec.Body.String())
	}
	choices, _ := decodeJSON(t, rec)["choices"].([]any)
	message, _ := choices[0].(map[string]any)["message"].(map[string]any)
	if message["content"] != refusal {
		t.Fatalf("content = %v, want the refusal", message["content"])
	}
}

// A long answer that merely discusses credits is an answer. Retrying it would
// spend two more accounts to produce the same text more slowly.
func TestChatCompletionsDoesNotMistakeALongAnswerForARefusal(t *testing.T) {
	long := strings.Repeat("积分不足的时候，系统会提示你。", 20)
	var attempts atomic.Int32
	h := newHarness(t, upstreamFunc(
		nil,
		func(string) string {
			attempts.Add(1)
			return upstreamText(long)
		},
	))
	h.addAccount(t, "a", "token-a", 1)
	h.addAccount(t, "b", "token-b", 2)

	rec := h.chat(t, map[string]any{
		"model":    "minimax-agent",
		"messages": []any{map[string]any{"role": "user", "content": "讲讲积分规则"}},
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if got := attempts.Load(); got != 1 {
		t.Fatalf("attempts = %d, want 1: a long answer is not a refusal", got)
	}
}

// A tool loop only works if the model can see its own previous calls. The
// upstream has no field for tool history, so both halves are folded into the
// flattened prompt in the block format the emulation asks for.
func TestChatCompletionsRendersToolHistory(t *testing.T) {
	h := newHarness(t, acceptsEverything(upstreamText("完成")))
	h.addAccount(t, "primary", "token-good", 10)
	seen := h.captureMessages(t)

	rec := h.chat(t, map[string]any{
		"model": "minimax-agent",
		"messages": []any{
			map[string]any{"role": "user", "content": "看看当前目录"},
			map[string]any{
				"role":    "assistant",
				"content": "",
				"tool_calls": []any{map[string]any{
					"id": "call_1", "type": "function",
					"function": map[string]any{"name": "list_dir", "arguments": `{"path":"."}`},
				}},
			},
			map[string]any{"role": "tool", "tool_call_id": "call_1", "name": "list_dir", "content": "a.txt\nb.txt"},
		},
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if len(*seen) == 0 {
		t.Fatal("no upstream turn was made")
	}
	prompt := (*seen)[0]
	if !strings.Contains(prompt, `<tool_calls>`) || !strings.Contains(prompt, `"name":"list_dir"`) {
		t.Fatalf("the previous call is missing from the prompt: %s", prompt)
	}
	if !strings.Contains(prompt, `{"path":"."}`) {
		t.Fatalf("the previous arguments are missing from the prompt: %s", prompt)
	}
	if !strings.Contains(prompt, "[工具结果 list_dir] a.txt") {
		t.Fatalf("the tool result is missing from the prompt: %s", prompt)
	}
}

// A tool turn that ends by narrating the next step is a dead end for the
// caller: it prints the sentence and waits for a call that never arrives. The
// turn is replayed once, with the narration folded back in and an explicit
// instruction to continue, and the caller only ever sees the retried answer.
func TestChatCompletionsReplaysATurnThatOnlyNarratesTheNextStep(t *testing.T) {
	var turns atomic.Int32
	h := newHarness(t, upstreamFunc(nil, func(string) string {
		if turns.Add(1) == 1 {
			return upstreamText("我先看看子文件夹里有什么，再写介绍。")
		}
		return upstreamText("继续。\n<tool_calls>\n{\"name\":\"exec_command\",\"arguments\":{\"cmd\":\"dir\"}}\n</tool_calls>")
	}))
	h.addAccount(t, "a", "token-a", 1)

	rec := h.chat(t, map[string]any{
		"model": "minimax-m3",
		"messages": []any{map[string]any{
			"role": "user", "content": "看一下文件夹然后写介绍",
		}},
		"tools": []any{map[string]any{
			"type": "function",
			"function": map[string]any{
				"name":       "exec_command",
				"parameters": map[string]any{"type": "object", "properties": map[string]any{"cmd": map[string]any{"type": "string"}}},
			},
		}},
		"stream": true,
	}, h.key)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if got := turns.Load(); got != 2 {
		t.Fatalf("upstream turns = %d, want the stalled turn replayed once", got)
	}
	body := rec.Body.String()
	if !strings.Contains(body, "tool_calls") || !strings.Contains(body, "exec_command") {
		t.Fatalf("the retried call never reached the caller: %s", body)
	}
	if strings.Contains(body, "我先看看子文件夹") {
		t.Fatalf("the stalled narration was shown to the caller: %s", body)
	}
}
