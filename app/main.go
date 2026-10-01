package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
)

// version is stamped at build time via:
//   go build -ldflags "-X main.version=1.2.3"
// The Dockerfile passes this through as a VERSION build arg per environment.
var version = "dev"

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))

	cfg, err := LoadConfig(os.Getenv, version)
	if err != nil {
		logger.Error("invalid configuration", "error", err)
		os.Exit(1)
	}

	srv := NewServer(cfg, logger)
	httpServer := &http.Server{
		Addr:    ":" + cfg.Port,
		Handler: srv.Routes(),
	}

	// Run the server in the background so main can block on signal handling
	// below instead of on ListenAndServe.
	serverErr := make(chan error, 1)
	go func() {
		logger.Info("starting server",
			"port", cfg.Port,
			"version", cfg.Version,
			"environment", cfg.Environment,
			"shutdown_timeout", cfg.ShutdownTimeout.String(),
		)
		if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			serverErr <- err
		}
	}()

	// SIGTERM is what Kubernetes sends when it terminates a pod (e.g. during
	// a rollout or scale-down); SIGINT covers Ctrl+C when running locally.
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGTERM, syscall.SIGINT)

	select {
	case err := <-serverErr:
		logger.Error("server failed to start", "error", err)
		os.Exit(1)
	case sig := <-stop:
		logger.Info("shutdown signal received, draining", "signal", sig.String())
	}

	// Fail readiness immediately so Kubernetes stops routing new requests
	// here, then give in-flight requests up to ShutdownTimeout to finish
	// before the listener is forcibly closed.
	srv.SetReady(false)

	ctx, cancel := context.WithTimeout(context.Background(), cfg.ShutdownTimeout)
	defer cancel()
	if err := httpServer.Shutdown(ctx); err != nil {
		logger.Error("graceful shutdown did not complete in time", "error", err)
		os.Exit(1)
	}
	logger.Info("shutdown complete")
}
// pickup-time test
