"use client";

import { useState } from "react";
import Link from "next/link";
import Logo from "../landing/Logo";
import { bffErrorMessage, postLogin, postRegister } from "@/lib/api/client";

const inputStyle: React.CSSProperties = {
  width: "100%",
  background: "var(--bk2)",
  border: "1px solid var(--gr3)",
  color: "var(--wh)",
  padding: "11px 12px",
  fontFamily: "var(--mono)",
  fontSize: 13,
  outline: "none",
};

export function AuthForm({ mode, next }: { mode: "login" | "register"; next: string }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isRegister = mode === "register";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (isRegister && password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (isRegister) await postRegister(email, password, name);
      else await postLogin(email, password);
      // Full navigation so every server component re-renders with the new session cookie.
      window.location.assign(next);
    } catch (err) {
      setError(bffErrorMessage(err));
      setBusy(false);
    }
  }

  const otherHref = `${isRegister ? "/login" : "/register"}${next !== "/dashboard" ? `?next=${encodeURIComponent(next)}` : ""}`;

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "var(--bk)", position: "relative" }}>
      <div className="lab-grid-bg" />
      <form onSubmit={submit} className="cd-panel" style={{ width: 380, position: "relative", zIndex: 2 }}>
        <div className="cd-panel-header" style={{ justifyContent: "center", padding: "18px 16px" }}>
          <Logo variant="sidebar" />
        </div>
        <div style={{ padding: "18px 22px 22px", display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ fontFamily: "var(--display)", fontSize: 14, fontWeight: 800, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--wh)" }}>
            {isRegister ? "Create an account" : "Log in"}
          </div>
          {isRegister && (
            <label>
              <div className="specimen" style={{ marginBottom: 4 }}>name (optional)</div>
              <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" style={inputStyle} />
            </label>
          )}
          <label>
            <div className="specimen" style={{ marginBottom: 4 }}>email</div>
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" style={inputStyle} />
          </label>
          <label>
            <div className="specimen" style={{ marginBottom: 4 }}>password</div>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={isRegister ? "new-password" : "current-password"}
              style={inputStyle}
            />
          </label>
          {isRegister && (
            <label>
              <div className="specimen" style={{ marginBottom: 4 }}>repeat password</div>
              <input type="password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" style={inputStyle} />
            </label>
          )}
          {error && <div style={{ fontSize: 13, color: "var(--rd)" }}>{error}</div>}
          <button
            type="submit"
            disabled={busy}
            style={{
              background: busy ? "var(--gr3)" : "var(--y)",
              color: busy ? "var(--gr)" : "var(--bk)",
              border: "none",
              padding: "12px 0",
              fontFamily: "var(--mono)",
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              cursor: busy ? "not-allowed" : "pointer",
            }}
          >
            {busy ? "…" : isRegister ? "Create account" : "Log in"}
          </button>
          <div style={{ fontSize: 13, color: "var(--gr)", textAlign: "center" }}>
            {isRegister ? "Already have an account? " : "No account yet? "}
            <Link href={otherHref} style={{ color: "var(--y)" }}>
              {isRegister ? "Log in" : "Create one"}
            </Link>
          </div>
        </div>
      </form>
    </div>
  );
}
