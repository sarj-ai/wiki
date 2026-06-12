"use client";

// Replaces the root layout on render errors above the in-app
// boundaries — must render its own <html>/<body>; globals.css and
// fonts aren't guaranteed here, hence inline styles. Color literals
// mirror the light-mode tokens in globals.css — keep them in sync.
export default function GlobalError({ reset }: { reset: () => void }) {
  return (
    <html lang="en">
      <body
        style={{
          display: "flex",
          minHeight: "100vh",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: "system-ui, sans-serif",
          background: "#f6efe7",
          color: "#231f20",
          margin: 0,
        }}
      >
        <div style={{ textAlign: "center", padding: "2rem" }}>
          <h1 style={{ fontSize: "1.25rem", marginBottom: "0.5rem" }}>Something went wrong</h1>
          <p style={{ marginBottom: "1.5rem", opacity: 0.7 }}>
            Sarj Wiki hit an unexpected error. Reloading usually fixes it.
          </p>
          <button
            type="button"
            onClick={reset}
            style={{
              padding: "0.5rem 1.25rem",
              borderRadius: "0.5rem",
              border: "1px solid #d5d5d5",
              background: "#fff",
              cursor: "pointer",
              font: "inherit",
            }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
