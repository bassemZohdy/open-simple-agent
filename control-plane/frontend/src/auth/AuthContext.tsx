import { createContext, type ReactNode, useContext, useMemo, useState } from "react";

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

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(readInitialToken);

  const value = useMemo<AuthContextValue>(
    () => ({
      token,
      setToken: (nextToken: string) => {
        const normalized = nextToken.trim();
        if (!normalized) return;
        sessionStorage.setItem(STORAGE_KEY, normalized);
        setTokenState(normalized);
      },
      clearToken: () => {
        sessionStorage.removeItem(STORAGE_KEY);
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
