/** Session: the bearer token (kept in localStorage) and the signed-in user from `/auth/me`. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, setToken, setUnauthorizedHandler } from "../api/client";
import type { Me, Role } from "../api/types";

const STORAGE_KEY = "kompas.token";

function readStored(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStored(value: string | null): void {
  try {
    if (value) localStorage.setItem(STORAGE_KEY, value);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Private mode or blocked storage: the session lives only in memory.
  }
}

interface TokenResponse {
  access_token: string;
  role: Role;
}

interface AuthState {
  me: Me | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<Me>;
  activate: (inviteCode: string, email: string, password: string) => Promise<Me>;
  register: (email: string, password: string, fullName: string) => Promise<Me>;
  /** A pending account enters a doctor's invite code and becomes that patient. */
  link: (inviteCode: string) => Promise<Me>;
  /** Demo mode: the same synthetic patient and their treating doctor. */
  demo: (kind: "patient" | "doctor") => Promise<Me>;
  signOut: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [token, setTokenState] = useState<string | null>(() => {
    const stored = readStored();
    setToken(stored);
    return stored;
  });

  const signOut = useCallback(() => {
    setToken(null);
    writeStored(null);
    setTokenState(null);
    queryClient.clear();
  }, [queryClient]);

  useEffect(() => {
    setUnauthorizedHandler(signOut);
    return () => setUnauthorizedHandler(null);
  }, [signOut]);

  const meQuery = useQuery({
    queryKey: ["auth", "me", token],
    queryFn: () => api.get<Me>("/auth/me"),
    enabled: token !== null,
    staleTime: Infinity,
    retry: false,
  });

  const start = useCallback(
    async (response: TokenResponse): Promise<Me> => {
      setToken(response.access_token);
      writeStored(response.access_token);
      const me = await api.get<Me>("/auth/me");
      queryClient.clear();
      queryClient.setQueryData(["auth", "me", response.access_token], me);
      setTokenState(response.access_token);
      return me;
    },
    [queryClient],
  );

  const value = useMemo<AuthState>(
    () => ({
      me: token ? (meQuery.data ?? null) : null,
      loading: token !== null && meQuery.isPending,
      signIn: async (email, password) => start(await api.post<TokenResponse>("/auth/login", { email, password })),
      activate: async (invite_code, email, password) =>
        start(await api.post<TokenResponse>("/auth/activate", { invite_code, email, password })),
      register: async (email, password, full_name) =>
        start(await api.post<TokenResponse>("/auth/register", { email, password, full_name })),
      link: async (invite_code) => start(await api.post<TokenResponse>("/auth/link", { invite_code })),
      demo: async (kind) => start(await api.post<TokenResponse>(`/demo/${kind}`, {})),
      signOut,
    }),
    [token, meQuery.data, meQuery.isPending, start, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth outside AuthProvider");
  return value;
}

/** The signed-in user; only for screens behind a role guard. */
export function useMe(): Me {
  const { me } = useAuth();
  if (!me) throw new Error("useMe without a session");
  return me;
}
