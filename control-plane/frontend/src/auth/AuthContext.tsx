import { createContext, type ReactNode, useContext, useEffect, useMemo, useState } from "react";

const STORAGE_KEY = "osa.control-panel.access-token";

interface AuthContextValue {
  token: string | null;
  setToken: (token: string) => void;
  clearToken: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function readInitialToken(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredToken(token: string): void {
  // F12: sessionStorage can throw (private mode, storage disabled); the token
  // then stays valid in memory for this session only.
  try {
    sessionStorage.setItem(STORAGE_KEY, token);
  } catch {
    // ignore
  }
}

function removeStoredToken(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(readInitialToken);

  // F11: an expired token surfaces as HTTP 401 from the API client; drop the
  // dead token so callers fall back to anonymous mode instead of retrying it.
  useEffect(() => {
    function onUnauthorized() {
      removeStoredToken();
      setTokenState(null);
    }
    window.addEventListener("osa:unauthorized", onUnauthorized);
    return () => window.removeEventListener("osa:unauthorized", onUnauthorized);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      token,
      setToken: (nextToken: string) => {
        const normalized = nextToken.trim();
        if (!normalized) return;
        writeStoredToken(normalized);
        setTokenState(normalized);
      },
      clearToken: () => {
        removeStoredToken();
        setTokenState(null);
      },
    }),
    [token],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return context;
}
