/** Loading and error states for queries. Short, no explanatory text beyond what failed. */
import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ApiError } from "../api/client";

export function Loading({ label = "Загрузка…" }: { label?: string }) {
  return (
    <div className="muted" role="status" style={{ padding: "12px 0" }}>
      {label}
    </div>
  );
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof TypeError) return "Нет связи с сервером";
  return "Что-то пошло не так";
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div role="alert" style={{ display: "flex", gap: 12, alignItems: "center", color: "#a4161a", padding: "12px 0" }}>
      <span>{errorText(error)}</span>
      {onRetry && (
        <button className="btn btn-t" type="button" onClick={onRetry}>
          Повторить
        </button>
      )}
    </div>
  );
}

/** Renders `children(data)` once the query has data; loading and error states otherwise. */
export function Q<T>({ query, children, loading }: { query: UseQueryResult<T>; children: (data: T) => ReactNode; loading?: ReactNode }) {
  if (query.isPending) return <>{loading ?? <Loading />}</>;
  if (query.isError) return <ErrorBox error={query.error} onRetry={() => void query.refetch()} />;
  return <>{children(query.data)}</>;
}
