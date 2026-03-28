package main

import (
	"bytes"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"os"
	"time"
)

const (
	defaultPort    = "8091"
	serviceVersion = "1.0.0"
)

var bridgeURL string

func main() {
	port := os.Getenv("SAFETY_GATE_PORT")
	if port == "" {
		port = defaultPort
	}
	bridgeURL = os.Getenv("BRIDGE_URL")
	if bridgeURL == "" {
		bridgeURL = "http://127.0.0.1:8090"
	}

	gate := NewSafetyGate()
	log.Printf("bridge_safety_gate v%s: port=%s bridge=%s", serviceVersion, port, bridgeURL)

	mux := http.NewServeMux()

	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status":   "ok",
			"service":  "bridge_safety_gate",
			"version":  serviceVersion,
			"language": "go",
			"bridge":   bridgeURL,
		})
	})

	// Intercept /robot/action — validate then forward
	mux.HandleFunc("/robot/action", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		body, err := io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, "read error", http.StatusBadRequest)
			return
		}

		var req ActionRequest
		if err := json.Unmarshal(body, &req); err != nil {
			http.Error(w, "invalid JSON", http.StatusBadRequest)
			return
		}

		t0 := time.Now()
		result := gate.Check(&req)
		result.LatencyUs = time.Since(t0).Microseconds()

		if result.Decision == "DENY" {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status":     "denied",
				"reason":     result.Reason,
				"rule_id":    result.RuleID,
				"audit_ref":  result.AuditRef,
				"latency_us": result.LatencyUs,
				"gate":       "go_bridge_safety_gate",
			})
			return
		}

		// ALLOW or LIMIT — forward to bridge (with clamped speed)
		if result.Decision == "LIMIT" {
			req.SpeedMps = result.ClampedSpeed
			req.TargetSpeedMps = result.ClampedSpeed
			body, _ = json.Marshal(req) // re-serialize with clamped speed
		}

		// Forward to Python bridge
		resp, err := http.Post(bridgeURL+"/robot/action", "application/json", bytes.NewReader(body))
		if err != nil {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status": "error",
				"reason": "bridge_unreachable: " + err.Error(),
				"gate":   "go_bridge_safety_gate",
			})
			return
		}
		defer resp.Body.Close()

		// Pass through bridge response
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(resp.StatusCode)
		io.Copy(w, resp.Body)
	})

	// Proxy all other endpoints to bridge
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		proxyURL := bridgeURL + r.URL.Path
		if r.URL.RawQuery != "" {
			proxyURL += "?" + r.URL.RawQuery
		}

		var body io.Reader
		if r.Body != nil {
			bodyBytes, _ := io.ReadAll(r.Body)
			body = bytes.NewReader(bodyBytes)
		}

		proxyReq, err := http.NewRequest(r.Method, proxyURL, body)
		if err != nil {
			http.Error(w, "proxy error", http.StatusBadGateway)
			return
		}
		proxyReq.Header = r.Header

		client := &http.Client{Timeout: 10 * time.Second}
		resp, err := client.Do(proxyReq)
		if err != nil {
			http.Error(w, "bridge unreachable", http.StatusBadGateway)
			return
		}
		defer resp.Body.Close()

		for k, v := range resp.Header {
			w.Header()[k] = v
		}
		w.WriteHeader(resp.StatusCode)
		io.Copy(w, resp.Body)
	})

	addr := ":" + port
	log.Printf("bridge_safety_gate listening on %s", addr)
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Fatalf("bridge_safety_gate: %v", err)
	}
}
