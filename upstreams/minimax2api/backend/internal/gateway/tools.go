package gateway

import (
	"encoding/json"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

// Function calling, emulated.
//
// There is no native channel for it. The upstream message body is built from a
// fixed field list — `content`, `attachments`, `model`, `turn_id`,
// `enable_team`, `client_intent`, `worktreeMode`, `workspace_dir` — and no
// member of that list carries a tool schema. That list is not a guess: it is
// the body builder in the web bundle's own message path, and the same bundle
// answers zero hits for `tools`, `functions` and `tool_choice` on the request
// side.
//
// What the upstream *does* return is `tool_call` frames — but those are its
// own server-side tools (`RunMcpTool`, `LoadToolkit`, `DownloadFile`,
// `RenderMermaid`, …), mounted by the agent and executed by the agent. They are
// not calls to anything the caller declared, so they cannot be forwarded as
// OpenAI `tool_calls` without lying about who is supposed to run them.
//
// So the only available mechanism is the same one every turn already has: the
// words in the turn. Tool declarations are appended to the prompt and the
// model is asked to answer in a tagged block. This is emulation and it is
// labelled as such everywhere it surfaces.
//
// Two deliberate departures from the obvious implementation:
//
//  1. The model is never silently swapped. Tool turns run on whatever model
//     the caller asked for. Quietly downgrading a model to make a format work
//     trades a visible failure for an invisible one.
//  2. A parse that finds nothing leaves the text alone. The block is only
//     removed when it was actually understood, so a model that answers with
//     something else produces prose rather than an empty message.

// toolSpec is one OpenAI-style function declaration, as received from a client.
type toolSpec struct {
	Type     string       `json:"type"`
	Function toolFunction `json:"function"`
}

type toolFunction struct {
	Name        string          `json:"name"`
	Description string          `json:"description"`
	Parameters  json.RawMessage `json:"parameters"`
}

// toolCall is one parsed call, already in the shape OpenAI reports.
type toolCall struct {
	ID        string
	Name      string
	Arguments string
}

const (
	toolOpenTag  = "<tool_calls>"
	toolCloseTag = "</tool_calls>"
)

// toolInstruction renders the declarations plus the answer format.
//
// The format is one JSON object per line inside the tag. A line-oriented JSON
// payload was chosen over the nested-XML shape because it needs no CDATA
// escaping and no attribute quoting: the model has fewer ways to get it
// almost-right, and the parser has fewer ways to accept something broken.
func toolInstruction(tools []toolSpec) string {
	var builder strings.Builder
	// The model is an agent with tools of its own in a cloud sandbox, and it
	// will happily use those instead — which would touch a throwaway Linux
	// workspace instead of the caller's machine. The instruction therefore
	// has to say both what these functions are and what they are not.
	builder.WriteString(
		"You are operating the user's own computer through the functions below. " +
			"They are the only way to read files, write files or run commands on it. " +
			"Do not use any built-in sandbox, workspace, shell or file tool, and do not " +
			"describe an action as done unless you emitted the call block.\n\n")
	for _, tool := range tools {
		name := strings.TrimSpace(tool.Function.Name)
		if name == "" {
			continue
		}
		builder.WriteString("Function: " + name + "\n")
		if desc := strings.TrimSpace(tool.Function.Description); desc != "" {
			builder.WriteString("Description: " + desc + "\n")
		}
		if len(tool.Function.Parameters) > 0 {
			builder.WriteString("Parameters (JSON Schema): " + string(tool.Function.Parameters) + "\n")
		}
		builder.WriteString("\n")
	}
	builder.WriteString(
		"When you decide to call one or more functions, end your reply with exactly this block " +
			"and nothing after it:\n\n" +
			toolOpenTag + "\n" +
			"{\"name\": \"function_name\", \"arguments\": {\"argument\": \"value\"}}\n" +
			toolCloseTag + "\n\n")
	// A worked example, not another rule. Live traffic showed the model
	// reading a tool name out of the caller's prompt, trying to invoke it as
	// one of its own tools, getting "not found" and stopping. Rules did not
	// move it; an exchange it can imitate is the one thing that does.
	builder.WriteString(exampleBlock(tools))
	builder.WriteString(
		"Rules:\n" +
			"- One JSON object per line inside the block, one line per call.\n" +
			"- `arguments` must be an object, not a string.\n" +
			"- Do not wrap the block in markdown fences.\n" +
			"- Never write a tool result yourself: results arrive in the next message.\n" +
			"- A function missing from your own tool list is expected. The block is how it is called.\n" +
			"- After a tool result, if the task has more steps, your next reply is another block. " +
			"Never describe the step you are about to take instead of taking it.\n" +
			"- If no function is needed, answer normally and omit the block entirely.\n")
	return builder.String()
}

// exampleBlock renders one complete exchange using the caller's first
// function, so the model sees the shape of a real turn rather than a
// placeholder name it has never seen.
func exampleBlock(tools []toolSpec) string {
	for _, tool := range tools {
		name := strings.TrimSpace(tool.Function.Name)
		if name == "" {
			continue
		}
		arguments := exampleArguments(tool.Function.Parameters)
		return "Example of a complete turn:\n\n" +
			"User: 看一下当前目录里有什么\n" +
			"Assistant: 我先列一下。\n" +
			toolOpenTag + "\n" +
			"{\"name\": " + strconv.Quote(name) + ", \"arguments\": " + arguments + "}\n" +
			toolCloseTag + "\n\n" +
			"Host: [工具结果 " + name + "] ...（宿主执行后返回的真实内容）\n\n" +
			"Assistant: 目录里有两个文件，分别是……\n\n"
	}
	return ""
}

// exampleArguments fills the first string field of a schema so the example
// uses the function's real parameter names.
func exampleArguments(schema json.RawMessage) string {
	var spec struct {
		Properties map[string]struct {
			Type string `json:"type"`
		} `json:"properties"`
		Required []string `json:"required"`
	}
	if json.Unmarshal(schema, &spec) != nil || len(spec.Properties) == 0 {
		return "{}"
	}
	for _, key := range spec.Required {
		if property, ok := spec.Properties[key]; ok && property.Type == "string" {
			return "{" + strconv.Quote(key) + ": \"...\"}"
		}
	}
	keys := make([]string, 0, len(spec.Properties))
	for key := range spec.Properties {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		if spec.Properties[key].Type == "string" {
			return "{" + strconv.Quote(key) + ": \"...\"}"
		}
	}
	return "{}"
}

// hasToolDeclarations reports whether any declaration is actually usable.
func hasToolDeclarations(tools []toolSpec) bool {
	for _, tool := range tools {
		if strings.TrimSpace(tool.Function.Name) != "" {
			return true
		}
	}
	return false
}

// injectTools appends the declarations to a flattened prompt.
//
// Appended rather than prepended: the instruction is about the shape of the
// next answer, and the end of the prompt is where that shape is decided. With
// the declaration first, a model with tools of its own read it once, drifted
// into its sandbox, and answered in prose — the calls never arrived.
func injectTools(prompt string, tools []toolSpec) string {
	if !hasToolDeclarations(tools) {
		return prompt
	}
	instruction := toolInstruction(tools)
	if prompt == "" {
		return instruction
	}
	return prompt + "\n\n" + instruction
}

// splitToolCalls separates a trailing call block from the prose before it.
//
// The returned text keeps every character that was not part of a block that
// parsed. A block that is present but malformed is left in place: dropping it
// would delete text the caller never got to see, and reporting a call that
// could not be parsed would be worse still.
func splitToolCalls(text string) (string, []toolCall) {
	open := strings.LastIndex(text, toolOpenTag)
	if open < 0 {
		return text, nil
	}
	closeAt := strings.Index(text[open:], toolCloseTag)
	if closeAt < 0 {
		return text, nil
	}
	closeAt += open

	body := text[open+len(toolOpenTag) : closeAt]
	calls := parseToolCallLines(body)
	if len(calls) == 0 {
		return text, nil
	}
	rest := strings.TrimRight(text[:open]+text[closeAt+len(toolCloseTag):], " \t\r\n")
	return rest, calls
}

// parseToolCallLines reads one JSON object per line.
func parseToolCallLines(body string) []toolCall {
	var calls []toolCall
	for _, line := range strings.Split(body, "\n") {
		line = strings.TrimSpace(line)
		line = strings.TrimSuffix(line, ",")
		if line == "" || !strings.HasPrefix(line, "{") {
			continue
		}
		var raw struct {
			Name      string          `json:"name"`
			Arguments json.RawMessage `json:"arguments"`
		}
		if err := json.Unmarshal([]byte(line), &raw); err != nil {
			continue
		}
		name := strings.TrimSpace(raw.Name)
		if name == "" {
			continue
		}
		// `arguments` is normalised to a JSON string, which is what the OpenAI
		// schema wants. A model that emits it as a string rather than an object
		// is accepted too — that mistake is common and unambiguous.
		arguments := "{}"
		if len(raw.Arguments) > 0 {
			if raw.Arguments[0] == '"' {
				var inner string
				if json.Unmarshal(raw.Arguments, &inner) == nil {
					arguments = inner
				}
			} else {
				arguments = string(raw.Arguments)
			}
		}
		calls = append(calls, toolCall{
			ID:        "call_" + randomID(24),
			Name:      name,
			Arguments: arguments,
		})
	}
	return calls
}

// toolCallsPayload renders parsed calls in the OpenAI wire shape.
func toolCallsPayload(calls []toolCall) []map[string]any {
	out := make([]map[string]any, 0, len(calls))
	for _, call := range calls {
		out = append(out, map[string]any{
			"id":   call.ID,
			"type": "function",
			"function": map[string]any{
				"name":      call.Name,
				"arguments": call.Arguments,
			},
		})
	}
	return out
}

// toolSplitter holds back a trailing call block while the rest streams.
//
// The block cannot be recognised until its opening tag has fully arrived, and
// it may straddle a chunk boundary — `"<tool_"` then `"calls>…"` is one tag in
// two frames. So the tail of every delta is withheld until it is long enough to
// decide. Only `len(toolOpenTag)-1` characters are ever delayed, and they are
// released as soon as the next delta rules them out, which keeps the delay
// below a single chunk in practice.
//
// Once the opening tag is seen nothing further is emitted as prose: everything
// from there on is either the block or text that followed it, and both are
// resolved by the parse at the end. Prose after a call block is unusual but
// legal, and dropping it would be a silent truncation.
type toolSplitter struct {
	enabled bool
	inBlock bool
	pending strings.Builder
}

func newToolSplitter(enabled bool) *toolSplitter {
	return &toolSplitter{enabled: enabled}
}

// push takes a delta and returns the part that is safe to emit now.
func (s *toolSplitter) push(text string) string {
	if !s.enabled {
		// No declarations in this turn, so there is no block to look for and
		// nothing to hold back.
		return text
	}
	if s.inBlock {
		s.pending.WriteString(text)
		return ""
	}
	s.pending.WriteString(text)
	buffer := s.pending.String()

	if index := strings.Index(buffer, toolOpenTag); index >= 0 {
		s.inBlock = true
		s.pending.Reset()
		s.pending.WriteString(buffer[index:])
		return buffer[:index]
	}

	// Nothing decided yet — hold back the longest tail that could still turn
	// out to be the start of an opening tag.
	keep := len(toolOpenTag) - 1
	if len(buffer) <= keep {
		return ""
	}
	// Cut on a rune boundary. The tag is ASCII, so backing off a byte or two
	// never costs any part of it — but slicing mid-character would hand the
	// client invalid UTF-8, and a streamed byte cannot be taken back.
	cut := len(buffer) - keep
	for cut > 0 && !utf8.RuneStart(buffer[cut]) {
		cut--
	}
	safe := buffer[:cut]
	s.pending.Reset()
	s.pending.WriteString(buffer[cut:])
	return safe
}

// finish flushes what is left and reports any calls that were held back.
func (s *toolSplitter) finish() (string, []toolCall) {
	if !s.enabled {
		return "", nil
	}
	buffer := s.pending.String()
	if !s.inBlock {
		return buffer, nil
	}
	return splitToolCalls(buffer)
}
