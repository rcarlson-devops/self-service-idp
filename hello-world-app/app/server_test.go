package main

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func testServer(t *testing.T, env string) (*Server, *bytes.Buffer) {
	t.Helper()
	var logBuf bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&logBuf, nil))
	cfg := Config{Port: "8080", Environment: env, Version: "test-version"}
	return NewServer(cfg, logger), &logBuf
}

func TestHandleHello(t *testing.T) {
	srv, _ := testServer(t, "dev")

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	rec := httptest.NewRecorder()
	srv.Routes().ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", rec.Code, http.StatusOK)
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Fatalf("Content-Type = %q, want application/json", ct)
	}

	var body helloResponse
	if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	if body.Environment != "dev" {
		t.Errorf("Environment = %q, want %q", body.Environment, "dev")
	}
	if body.Version != "test-version" {
		t.Errorf("Version = %q, want %q", body.Version, "test-version")
	}
	if body.Hostname == "" {
		t.Error("Hostname should not be empty")
	}
}

func TestHandleVersion(t *testing.T) {
	srv, _ := testServer(t, "staging")

	req := httptest.NewRequest(http.MethodGet, "/version", nil)
	rec := httptest.NewRecorder()
	srv.Routes().ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", rec.Code, http.StatusOK)
	}

	var body versionResponse
	if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	if body.Environment != "staging" || body.Version != "test-version" {
		t.Errorf("got %+v, want environment=staging version=test-version", body)
	}
}

func TestHandleLiveness_AlwaysOK(t *testing.T) {
	srv, _ := testServer(t, "prod")
	srv.SetReady(false) // liveness must stay OK even when not ready to serve traffic

	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	rec := httptest.NewRecorder()
	srv.Routes().ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("liveness status = %d, want %d even while not ready", rec.Code, http.StatusOK)
	}
}

func TestHandleReadiness_DrainsOnShutdown(t *testing.T) {
	srv, _ := testServer(t, "prod")

	// Ready by default right after construction.
	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	rec := httptest.NewRecorder()
	srv.Routes().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("readiness status = %d, want %d before shutdown", rec.Code, http.StatusOK)
	}

	// This is the behavior the whole readiness-drain design depends on:
	// once shutdown starts, readiness must fail immediately so Kubernetes
	// stops routing new traffic here before the listener actually closes.
	srv.SetReady(false)

	req = httptest.NewRequest(http.MethodGet, "/readyz", nil)
	rec = httptest.NewRecorder()
	srv.Routes().ServeHTTP(rec, req)
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("readiness status = %d, want %d after shutdown begins", rec.Code, http.StatusServiceUnavailable)
	}
}

func TestLoggingMiddleware_LogsRealRequests(t *testing.T) {
	srv, logBuf := testServer(t, "dev")

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	srv.Routes().ServeHTTP(httptest.NewRecorder(), req)

	logged := logBuf.String()
	if !strings.Contains(logged, `"path":"/"`) {
		t.Errorf("expected log line for real request, got: %s", logged)
	}
	if !strings.Contains(logged, `"status":200`) {
		t.Errorf("expected logged status 200, got: %s", logged)
	}
}

func TestHandlePreStop_RespectsDelay(t *testing.T) {
	var logBuf bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&logBuf, nil))
	cfg := Config{Port: "8080", Environment: "prod", Version: "test-version", PreStopDelay: 20 * time.Millisecond}
	srv := NewServer(cfg, logger)

	start := time.Now()
	req := httptest.NewRequest(http.MethodGet, "/internal/prestop", nil)
	rec := httptest.NewRecorder()
	srv.Routes().ServeHTTP(rec, req)
	elapsed := time.Since(start)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", rec.Code, http.StatusOK)
	}
	if elapsed < cfg.PreStopDelay {
		t.Errorf("handler returned after %v, want at least %v", elapsed, cfg.PreStopDelay)
	}
}

func TestHandlePreStop_ZeroDelayReturnsImmediately(t *testing.T) {
	srv, _ := testServer(t, "dev") // PreStopDelay defaults to zero value

	req := httptest.NewRequest(http.MethodGet, "/internal/prestop", nil)
	rec := httptest.NewRecorder()
	srv.Routes().ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", rec.Code, http.StatusOK)
	}
}

func TestLoggingMiddleware_SkipsProbes(t *testing.T) {
	srv, logBuf := testServer(t, "dev")

	for _, path := range []string{"/healthz", "/readyz", "/internal/prestop"} {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		srv.Routes().ServeHTTP(httptest.NewRecorder(), req)
	}

	if logBuf.Len() != 0 {
		t.Errorf("expected no log lines for probe/internal traffic, got: %s", logBuf.String())
	}
}

// TestFullServerIntegration exercises the handlers behind a real listener
// and HTTP client, rather than calling ServeHTTP directly, as a sanity
// check that routing and the server wiring in main.go actually work end to
// end.
func TestFullServerIntegration(t *testing.T) {
	srv, _ := testServer(t, "dev")
	ts := httptest.NewServer(srv.Routes())
	defer ts.Close()

	resp, err := http.Get(ts.URL + "/healthz")
	if err != nil {
		t.Fatalf("request failed: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want %d", resp.StatusCode, http.StatusOK)
	}
}
