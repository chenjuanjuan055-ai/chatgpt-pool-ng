package main

// opscan asks every account what its H3 entitlement actually is.
//
// The pool's "credits" figure comes from `op_credit_summary.total_remaining_amount`,
// which is not the balance H3 bills against: H3 needs `opcredit_balance` plus a
// plan whose `feature_entitlements.video_h3.allowed` is true. Those are separate
// fields in the same answer, so a pool full of "credits" can still be unable to
// run a single H3 task — and that is exactly the question this answers.
//
//	opscan <app.json> [workers]
//
// Read-only: it posts the same membership query the console does.

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strconv"
	"sync"
	"time"

	"minimax2api/internal/config"
	"minimax2api/internal/minimax"
)

type account struct {
	Name     string `json:"name"`
	Region   string `json:"region"`
	UserID   string `json:"userId"`
	AgentID  string `json:"agentID"`
	DeviceID string `json:"deviceID"`
	UUID     string `json:"uuid"`
	Token    string `json:"token"`
	Enabled  bool   `json:"enabled"`
}

type appFile struct {
	Accounts []account       `json:"accounts"`
	Settings json.RawMessage `json:"settings"`
}

type membership struct {
	OpCreditBalance json.Number `json:"opcredit_balance"`
	Summary         struct {
		Total string `json:"total_remaining_amount"`
	} `json:"op_credit_summary"`
	Entitlements struct {
		VideoH3 struct {
			Allowed bool `json:"allowed"`
			Denial  struct {
				ErrorCode string `json:"error_code"`
				Plan      string `json:"plan_key"`
			} `json:"entitlement_denial"`
		} `json:"video_h3"`
	} `json:"feature_entitlements"`
}

type row struct {
	Name     string
	OpCredit string
	Total    string
	H3       bool
	Code     string
	Plan     string
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("usage: opscan <app.json> [workers]")
		os.Exit(2)
	}
	raw, err := os.ReadFile(os.Args[1])
	if err != nil {
		fmt.Println("read:", err)
		os.Exit(1)
	}
	var app appFile
	if err := json.Unmarshal(raw, &app); err != nil {
		fmt.Println("parse:", err)
		os.Exit(1)
	}
	settings := config.DefaultSettings(os.TempDir())
	if len(app.Settings) > 0 {
		_ = json.Unmarshal(app.Settings, &settings)
	}
	client := minimax.New(func() config.Settings { return settings })

	workers := 4
	if len(os.Args) > 2 {
		workers, _ = strconv.Atoi(os.Args[2])
	}

	jobs := make(chan account)
	results := make([]row, 0, len(app.Accounts))
	var mu sync.Mutex
	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for acc := range jobs {
				cred := minimax.Credential{
					Region: acc.Region, Token: acc.Token, UserID: acc.UserID,
					AgentID: acc.AgentID, DeviceID: acc.DeviceID, UUID: acc.UUID,
					ScreenWidth: 1536, ScreenHeight: 864,
				}
				ctx, cancel := context.WithTimeout(context.Background(), 40*time.Second)
				probe, err := client.FetchRaw(ctx, cred, "POST",
					"/matrix/api/v1/commerce/get_membership_info", nil, false, 1<<20)
				cancel()
				r := row{Name: acc.Name}
				if err != nil {
					r.Code = "ERR"
				} else {
					var m membership
					if json.Unmarshal([]byte(probe.Body), &m) == nil {
						r.OpCredit = string(m.OpCreditBalance)
						r.Total = m.Summary.Total
						r.H3 = m.Entitlements.VideoH3.Allowed
						r.Code = m.Entitlements.VideoH3.Denial.ErrorCode
						r.Plan = m.Entitlements.VideoH3.Denial.Plan
					} else {
						r.Code = "BADJSON"
					}
				}
				mu.Lock()
				results = append(results, r)
				mu.Unlock()
			}
		}()
	}
	for _, acc := range app.Accounts {
		if acc.Enabled {
			jobs <- acc
		}
	}
	close(jobs)
	wg.Wait()

	h3OK, opPositive, errs := 0, 0, 0
	for _, r := range results {
		if r.H3 {
			h3OK++
		}
		if r.Code == "ERR" {
			errs++
		}
		if n, err := strconv.ParseFloat(r.OpCredit, 64); err == nil && n > 0 {
			opPositive++
		}
	}
	fmt.Printf("scanned=%d h3_allowed=%d opcredit>0=%d errors=%d\n", len(results), h3OK, opPositive, errs)

	sort.Slice(results, func(i, j int) bool {
		a, _ := strconv.ParseFloat(results[i].OpCredit, 64)
		b, _ := strconv.ParseFloat(results[j].OpCredit, 64)
		return a > b
	})
	fmt.Println("--- 前 15 个 opcredit 最高的号 ---")
	for i, r := range results {
		if i >= 15 {
			break
		}
		fmt.Printf("%-22s op=%-12s total=%-12s h3=%-5v %s/%s\n", r.Name, r.OpCredit, r.Total, r.H3, r.Code, r.Plan)
	}
}
