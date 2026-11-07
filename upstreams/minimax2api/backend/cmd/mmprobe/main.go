package main

// mmprobe 复刻网关的真实调用：用 app.json 里的 settings 去套 DefaultSettings，
// 再对指定账号跑 FetchUserInfo / Prepare，把完整错误打出来（只读，不改任何数据）。
//
//	mmprobe <app.json> <startIndex> <count>

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
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
	Accounts []account       `json:"accounts"`
	Settings json.RawMessage `json:"settings"`
}

func main() {
	path := os.Args[1]
	start, _ := strconv.Atoi(os.Args[2])
	count, _ := strconv.Atoi(os.Args[3])

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
	if len(app.Settings) > 0 {
		if err := json.Unmarshal(app.Settings, &settings); err != nil {
			fmt.Println("apply settings:", err)
		} else {
			fmt.Println("[已套用 app.json 的 settings]")
		}
	}
	fmt.Printf("[upstream] baseURL=%s screen=%dx%d lang=%q proxy=%q ua=%s\n",
		settings.Upstream.BaseURL, settings.Upstream.ScreenWidth, settings.Upstream.ScreenHeight,
		settings.Signin.Lang, settings.Upstream.Proxy, settings.Upstream.UserAgent)

	client := minimax.New(func() config.Settings { return settings })

	end := start + count
	if end > len(app.Accounts) {
		end = len(app.Accounts)
	}
	for _, a := range app.Accounts[start:end] {
		cred := minimax.Credential{
			Region:   minimax.RegionGlobal,
			Token:    a.Token,
			UserID:   a.UserID,
			AgentID:  a.AgentID,
			DeviceID: a.DeviceID,
			UUID:     a.UUID,
		}
		ctx, cancel := context.WithTimeout(context.Background(), 40*time.Second)
		info, err := client.FetchUserInfo(ctx, cred)
		cancel()
		fmt.Printf("-- %s (userId=%q agentID=%q)\n", a.Name, a.UserID, a.AgentID)
		if err != nil {
			fmt.Printf("   FetchUserInfo ERR: %v\n", err)
		} else {
			fmt.Printf("   FetchUserInfo OK: realUserID=%s name=%q email=%q phone=%q\n",
				info.RealUserID, info.Name, info.Email, info.Phone)
		}
		ctx2, cancel2 := context.WithTimeout(context.Background(), 60*time.Second)
		prep, perr := client.Prepare(ctx2, cred)
		cancel2()
		if perr != nil {
			fmt.Printf("   Prepare ERR: %v\n", perr)
		} else {
			fmt.Printf("   Prepare OK: agentID=%v\n", prep.AgentID())
		}
		time.Sleep(300 * time.Millisecond)
	}
}
