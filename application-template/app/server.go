package main

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"os"
	"sync/atomic"
	"time"
)

// Server wires configuration and readiness state to HTTP handlers.
type Server struct {
	cfg    Config
	logger *slog.Logger
	ready  atomic.Bool
}

// NewServer constructs a Server that reports ready immediately. Call
// SetReady(false) when shutdown begins so /readyz starts failing before the
// process actually stops accepting connections.
func NewServer(cfg Config, logger *slog.Logger) *Server {
	s := &Server{cfg: cfg, logger: logger}
	s.ready.Store(true)
	return s
}

func (s *Server) SetReady(ready bool) {
	s.ready.Store(ready)
}

func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/", s.handleHello)
	mux.HandleFunc("/version", s.handleVersion)
	mux.HandleFunc("/healthz", s.handleLiveness)
	mux.HandleFunc("/readyz", s.handleReadiness)
	mux.HandleFunc("/internal/prestop", s.handlePreStop)
	return s.withLogging(mux)
}

type helloResponse struct {
	Message     string    `json:"message"`
	Hostname    string    `json:"hostname"`
	Version     string    `json:"version"`
	Environment string    `json:"environment"`
	Timestamp   time.Time `json:"timestamp"`
}

func (s *Server) handleHello(w http.ResponseWriter, r *http.Request) {
	hostname, err := os.Hostname()
	if err != nil {
		hostname = "unknown"
	}
	writeJSON(w, http.StatusOK, helloResponse{
		Message:     "Hello from the internal developer platform!",
		Hostname:    hostname,
		Version:     s.cfg.Version,
		Environment: s.cfg.Environment,
		Timestamp:   time.Now().UTC(),
	})
}

type versionResponse struct {
	Version     string `json:"version"`
	Environment string `json:"environment"`
}

// handleVersion is a lightweight endpoint for scripts (e.g. the golden path
// doc's "verify what actually deployed" step) that don't want to parse the
// full hello payload just to check a version string.
func (s *Server) handleVersion(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, versionResponse{
		Version:     s.cfg.Version,
		Environment: s.cfg.Environment,
	})
}

// handleLiveness answers "is the process alive". It should essentially
// never fail — Kubernetes restarts the pod when it does, which is the
// wrong response to "traffic should stop routing here for a moment."
func (s *Server) handleLiveness(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
	w.Write([]byte("ok"))
}

// handleReadiness answers "should this pod receive traffic right now". It
// flips to false the instant graceful shutdown starts (see main.go), so
// Kubernetes stops sending new requests here before the process stops
// accepting connections — the difference between a clean rollout and
// dropped requests during every deploy.
func (s *Server) handleReadiness(w http.ResponseWriter, r *http.Request) {
	if !s.ready.Load() {
		http.Error(w, "shutting down", http.StatusServiceUnavailable)
		return
	}
	w.WriteHeader(http.StatusOK)
	w.Write([]byte("ok"))
}

// handlePreStop backs Kubernetes' preStop lifecycle hook, called via
// httpGet rather than exec so it works on a shell-less distroless image.
// It simply waits out PreStopDelay before responding, giving endpoint and
// ingress controllers time to notice this pod is no longer ready before
// SIGTERM triggers the app's own graceful shutdown.
func (s *Server) handlePreStop(w http.ResponseWriter, r *http.Request) {
	if s.cfg.PreStopDelay > 0 {
		time.Sleep(s.cfg.PreStopDelay)
	}
	w.WriteHeader(http.StatusOK)
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

// withLogging emits one structured JSON log line per request, tagged with
// the environment so logs are easy to filter once they're shipped
// somewhere central. Probe traffic (/healthz, /readyz) is skipped — it
// fires every few seconds per pod and would otherwise drown out real
// request logs.
func (s *Server) withLogging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/healthz" || r.URL.Path == "/readyz" || r.URL.Path == "/internal/prestop" {
			next.ServeHTTP(w, r)
			return
		}

		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rec, r)

		s.logger.Info("request",
			"method", r.Method,
			"path", r.URL.Path,
			"status", rec.status,
			"duration_ms", time.Since(start).Milliseconds(),
			"environment", s.cfg.Environment,
		)
	})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
