package main

// mmfetch lists what a session produced and downloads the media it finds.
//
//	MM_SESSION=<id> MM_OUT=<dir> ./mmfetch
//
// Credentials: MM_TOKEN, MM_USER_ID, MM_AGENT_ID, MM_DEVICE_ID, MM_UUID, MM_PROXY.

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"minimax2api/internal/config"
	"minimax2api/internal/minimax"
)

func main() {
	cred := minimax.Credential{
		Region:       minimax.RegionGlobal,
		Token:        os.Getenv("MM_TOKEN"),
		UserID:       os.Getenv("MM_USER_ID"),
		AgentID:      os.Getenv("MM_AGENT_ID"),
		DeviceID:     os.Getenv("MM_DEVICE_ID"),
		UUID:         os.Getenv("MM_UUID"),
		ScreenWidth:  1536,
		ScreenHeight: 864,
	}
	session := os.Getenv("MM_SESSION")
	outDir := os.Getenv("MM_OUT")
	if cred.Token == "" || cred.UserID == "" {
		fmt.Println("need MM_TOKEN and MM_USER_ID")
		os.Exit(2)
	}

	settings := config.DefaultSettings(os.TempDir())
	settings.Upstream.Proxy = os.Getenv("MM_PROXY")
	settings.Upstream.ScreenWidth = 1536
	settings.Upstream.ScreenHeight = 864
	client := minimax.New(func() config.Settings { return settings })

	ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	defer cancel()

	if session == "" {
		var err error
		session, err = findSession(ctx, client, cred, os.Getenv("MM_TITLE"))
		if err != nil {
			fmt.Printf("LIST_ERROR %v\n", err)
			os.Exit(1)
		}
		fmt.Printf("SESSION %s\n", session)
	}

	arts, err := client.SessionArtifacts(ctx, cred, session)
	if err != nil {
		fmt.Printf("SESSION_ERROR %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("ARTIFACTS %d\n", len(arts))
	for _, a := range arts {
		fmt.Printf("  kind=%s category=%s mime=%s ext=%s size=%d created=%s name=%s node=%s\n",
			a.Kind(), a.Category, a.MimeType, a.FileExt, a.SizeBytes,
			time.UnixMilli(a.CreatedAt).Format("2006-01-02 15:04:05"), a.Name, a.NodeID)
	}

	for _, a := range arts {
		kind := a.Kind()
		if kind != "video" && kind != "image" {
			continue
		}
		url, err := client.DownloadURL(ctx, cred, a.NodeID)
		if err != nil || url == "" {
			fmt.Printf("  URL_FAIL node=%s %v\n", a.NodeID, err)
			continue
		}
		if outDir == "" {
			fmt.Printf("  URL_OK node=%s (no MM_OUT, not downloading)\n", a.NodeID)
			continue
		}
		target := filepath.Join(outDir, "recovered_"+a.NodeID+a.FileExt)
		n, err := download(ctx, url, target)
		if err != nil {
			fmt.Printf("  DOWNLOAD_FAIL node=%s %v\n", a.NodeID, err)
			continue
		}
		fmt.Printf("  DOWNLOAD_OK %s %d bytes\n", target, n)
	}
}

func findSession(ctx context.Context, client *minimax.Client, cred minimax.Credential, title string) (string, error) {
	path := "/minimax-cloud/api/v1/agent/" + cred.AgentID + "/session"
	probe, err := client.FetchRaw(ctx, cred, http.MethodGet, path, nil, false, 1<<20)
	if err != nil {
		return "", err
	}
	var parsed struct {
		Sessions []struct {
			SessionID string `json:"session_id"`
			Title     string `json:"title"`
			UpdatedAt int64  `json:"updated_at"`
		} `json:"sessions"`
	}
	if err := json.Unmarshal([]byte(probe.Body), &parsed); err != nil {
		return "", fmt.Errorf("parse session list: %w", err)
	}
	for _, s := range parsed.Sessions {
		if s.SessionID == "" {
			continue
		}
		if title == "" || strings.Contains(s.Title, title) {
			fmt.Printf("  cand %s %s %s\n", s.SessionID,
				time.UnixMilli(s.UpdatedAt).Format("01-02 15:04:05"), s.Title)
			if title != "" {
				return s.SessionID, nil
			}
		}
	}
	return "", fmt.Errorf("no session matched %q", title)
}

func download(ctx context.Context, url, target string) (int64, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return 0, err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return 0, fmt.Errorf("status %d", resp.StatusCode)
	}
	file, err := os.Create(target)
	if err != nil {
		return 0, err
	}
	defer file.Close()
	return io.Copy(file, io.LimitReader(resp.Body, 512<<20))
}
