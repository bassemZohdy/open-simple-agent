import { useMemo } from "react";

import { useAuth } from "../auth/AuthContext";
import { ControlPlaneClient, defaultApiBaseUrl } from "./client";

export function useControlPlaneClient(): ControlPlaneClient {
  const { token } = useAuth();
  return useMemo(() => new ControlPlaneClient(defaultApiBaseUrl, () => token), [token]);
}
