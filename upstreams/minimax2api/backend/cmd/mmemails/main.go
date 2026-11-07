package main

// mmemails prints每个 MiniMax 账号在上游登记的邮箱 / 手机号。
//
//	mmemails [path/to/app.json]
//
// 复用网关自己的签名逻辑（internal/minimax.FetchUserInfo），不改任何数据。

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"minimax2api/internal/config"
	"minimax2api/internal/minimax"
)

type account struct {
	ID         string `json:"id"`
	Name       string `json:"name"`
	Region     string `json:"region"`
	UserID     string `json:"userId"`
	Identifier string `json:"identifier"`
	AgentID    string `json:"agentID"`
	DeviceID   string `json:"deviceID"`
	UUID       string `json:"uuid"`
	Token      string `json:"token"`
}

type appFile struct {
	Accounts []account `json:"accounts"`
}

func main() {
	path := "/opt/minimax2api/data/app.json"
	if len(os.Args) > 1 {
		path = os.Args[1]
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		fmt.Println("read app.json:", err)
		os.Exit(1)
	}
	var app appFile
	if err := json.Unmarshal(raw, &app); err != nil {
		fmt.Println("parse app.json:", err)
		os.Exit(1)
	}

	settings := config.DefaultSettings(os.TempDir())
	if px := os.Getenv("MM_PROXY"); px != "" {
		settings.Upstream.Proxy = px
	}
	settings.Upstream.ScreenWidth = 1536
	settings.Upstream.ScreenHeight = 864
	client := minimax.New(func() config.Settings { return settings })

	fmt.Printf("共 %d 个账号\n\n", len(app.Accounts))
	fmt.Printf("%-14s %-20s %-32s %-18s %s\n", "名称", "identifier", "邮箱", "手机号", "realUserID")
	fmt.Println("-----------------------------------------------------------------------------------------------")
	for _, a := range app.Accounts {
		cred := minimax.Credential{
			Region:       minimax.RegionGlobal,
			Token:        a.Token,
			UserID:       a.UserID,
			AgentID:      a.AgentID,
			DeviceID:     a.DeviceID,
			UUID:         a.UUID,
			ScreenWidth:  1536,
			ScreenHeight: 864,
		}
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		info, err := client.FetchUserInfo(ctx, cred)
		cancel()
		if err != nil {
			fmt.Printf("%-14s %-20s ERR: %v\n", a.Name, a.Identifier, err)
			continue
		}
		fmt.Printf("%-14s %-20s %-32s %-18s %s\n", a.Name, a.Identifier, dash(info.Email), dash(info.Phone), info.RealUserID)
		time.Sleep(250 * time.Millisecond)
	}
}

func dash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}
