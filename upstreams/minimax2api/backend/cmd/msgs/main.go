package main

// msgs dumps a session's message history verbatim.
//
// driveprobe prints the *shape* of a session document because the summaries are
// somebody's conversation. This one prints the text, and it exists for the one
// question a shape cannot answer: the agent's finished turn is delivered as a
// `<deliver-assets>` block inside the assistant message, and whether that block
// carries a fetchable URL or a path inside the agent's own workspace is the
// difference between "the gateway is not looking" and "there is nothing to
// look at". Only the raw text settles it.
//
//	MM_SESSION=<id> MM_OUT=<file> ./msgs
//	MM_PATH=<path>  MM_OUT=<file> ./msgs      # any signed GET, verbatim
//	MM_METHOD=DELETE ./msgs                   # override the verb
//
// Credentials: MM_TOKEN, MM_USER_ID, MM_DEVICE_ID, MM_UUID, MM_PROXY.

import (
	"context"
	"fmt"
	"os"
	"time"

	"minimax2api/internal/config"
	"minimax2api/internal/minimax"
)

// historyLimit is well above the largest history seen so far (~163 kB).
const historyLimit = 16 << 20

func main() {
	cred := minimax.Credential{
		Region:       minimax.RegionGlobal,
		Token:        os.Getenv("MM_TOKEN"),
		UserID:       os.Getenv("MM_USER_ID"),
		DeviceID:     os.Getenv("MM_DEVICE_ID"),
		UUID:         os.Getenv("MM_UUID"),
		ScreenWidth:  1536,
		ScreenHeight: 864,
	}
	if cred.Token == "" || cred.UserID == "" {
		fmt.Println("need MM_TOKEN and MM_USER_ID")
		os.Exit(2)
	}

	path := os.Getenv("MM_PATH")
	method := os.Getenv("MM_METHOD")
	if method == "" {
		method = "GET"
	}
	if path == "" {
		session := os.Getenv("MM_SESSION")
		if session == "" {
			fmt.Println("need one of MM_SESSION / MM_PATH")
			os.Exit(2)
		}
		path = "/minimax-cloud/api/v1/session/" + session + "/message"
	}

	settings := config.DefaultSettings(os.TempDir())
	settings.Upstream.Proxy = os.Getenv("MM_PROXY")
	settings.Upstream.ScreenWidth = 1536
	settings.Upstream.ScreenHeight = 864
	client := minimax.New(func() config.Settings { return settings })

	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()

	probe, err := client.FetchRaw(ctx, cred, method, path, nil, false, historyLimit)
	if err != nil {
		fmt.Printf("FETCH_ERROR %v\n", err)
		os.Exit(1)
	}
	if target := os.Getenv("MM_OUT"); target != "" {
		if err := os.WriteFile(target, []byte(probe.Body), 0o600); err != nil {
			fmt.Printf("WRITE_ERROR %v\n", err)
			os.Exit(1)
		}
		fmt.Printf("WROTE %s %d bytes (status %d)\n", target, len(probe.Body), probe.Status)
		return
	}
	fmt.Printf("status %d, %d bytes\n%s\n", probe.Status, len(probe.Body), probe.Body)
}
