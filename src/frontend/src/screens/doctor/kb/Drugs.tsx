/**
 * Board K-Drugs: the ОХЛП base by МНН on open, or a search (`GET /drugs?q`), loaded in pages as the list scrolls,
 * and a short card of the selected label;
 * `DrugLabelPage` (/d/kb/drugs/:labelId) shows the whole record, every value with its section and quote.
 * Search fields and dosage forms are applied by the API before pagination.
 */
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { api } from "../../../api/client";
import type { DrugLabel, DrugSearchItem, Page } from "../../../api/types";
import { clip } from "../../../lib/labelText";
import { useScreen } from "../../../ui/screen";
import { ErrorBox, Loading, Q } from "../../../ui/states";
import { PREVIEW_KEYS, capitalize, factRows, groupBySubstance, maxDaily, type FactRow } from "./drugFacts";
import "./Drugs.css";

const PAGE = 30;


function useKnowledgeVersion() {
  return useQuery({
    queryKey: ["kb", "version"],
    queryFn: () => api.get<{ version: string; drugs_loaded: boolean }>("/knowledge/version"),
    staleTime: Infinity,
  });
}

function useLabel(labelId: string | null | undefined) {
  return useQuery({
    queryKey: ["kb", "drug", labelId],
    queryFn: () => api.get<DrugLabel>(`/drugs/${encodeURIComponent(labelId ?? "")}`),
    enabled: Boolean(labelId),
    staleTime: Infinity,
  });
}

/** Whether the element is within 400px of the viewport. */
function useNearViewport() {
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  const [near, setNear] = useState(false);
  useEffect(() => {
    if (!node) return;
    const observer = new IntersectionObserver((entries) => setNear(entries.some((entry) => entry.isIntersecting)), { rootMargin: "400px" });
    observer.observe(node);
    return () => {
      observer.disconnect();
      setNear(false);
    };
  }, [node]);
  return [setNode, near] as const;
}

function SearchIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#16161a" strokeWidth="1.8" aria-hidden="true" style={{ top: 13, left: 13 }}>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-4-4" />
    </svg>
  );
}

function LongText({ text, limit = 360 }: { text: string; limit?: number }) {
  const [open, setOpen] = useState(false);
  if (text.length <= limit) return <div className="fv">{text}</div>;
  return (
    <div className="fv">
      {open ? text : clip(text, limit)}{" "}
      <button className="more" type="button" onClick={() => setOpen(!open)}>
        {open ? "Свернуть" : "Полностью"}
      </button>
    </div>
  );
}

export function FactSection({ row, extra }: { row: FactRow; extra?: string | null }) {
  return (
    <div className={`sec${row.missing ? " nd" : ""}`}>
      <b>{row.section ?? "—"}</b>
      <div className="fb">
        <div className="sub">{row.name}</div>
        {row.missing ? (
          <span className="chip nd">нет данных</span>
        ) : row.level ? (
          <span className={`lv lv-${row.level}`}>{row.value}</span>
        ) : (
          <LongText text={row.value} />
        )}
        {extra && <span className="chip">{extra}</span>}
        {row.quote && <blockquote>«{row.quote}»</blockquote>}
      </div>
    </div>
  );
}

function LabelHead({ label, action }: { label: DrugLabel; action?: React.ReactNode }) {
  return (
    <>
      <div className="ch">
        <div>
          <div className="lt">{label.display_name ?? `${label.trade_name}, ${label.form}`}</div>
          <div className="sub">{[label.substances.join(" + "), label.atc].filter(Boolean).join(" · ")}</div>
        </div>
        {action}
      </div>
      {(label.groups.length > 0 || label.antidepressant_profile || label.needs_check) && (
        <div className="pad" style={{ display: "flex", gap: 6, flexWrap: "wrap", borderBottom: "1px solid var(--rule)" }}>
          {label.needs_check && (
            <span className="lv lv-amb" title={typeof label.needs_check_reason === "string" ? label.needs_check_reason : undefined}>
              Требует проверки
            </span>
          )}
          {label.groups.map((g) => (
            <span className="chip" key={g}>
              Группа: {g}
            </span>
          ))}
          {label.antidepressant_profile && <span className="chip">Профиль: {label.antidepressant_profile}</span>}
        </div>
      )}
    </>
  );
}

function Preview({ labelId }: { labelId: string }) {
  const label = useLabel(labelId);
  return (
    <section className="card">
      <Q query={label} loading={<div className="pad"><Loading /></div>}>
        {(data) => (
          <>
            <LabelHead
              label={data}
              action={
                <Link className="btn btn-t" to={`/d/kb/drugs/${data.id}`}>
                  ОХЛП целиком
                </Link>
              }
            />
            {factRows(data as unknown as Record<string, unknown>, PREVIEW_KEYS).map((row) => (
              <FactSection key={row.key} row={row} />
            ))}
          </>
        )}
      </Q>
    </section>
  );
}

export function Drugs() {
  useScreen("k-drugs");
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const substance = params.get("substance") ?? "";
  const tradeName = params.get("trade_name") ?? "";
  const selectedForm = params.get("forms");
  const [filterOpen, setFilterOpen] = useState(false);
  const allForms = !params.has("forms");
  const selected = params.get("label");
  const version = useKnowledgeVersion();
  const forms = useQuery({
    queryKey: ["kb", "drug-forms"],
    queryFn: () => api.get<string[]>("/drugs/forms"),
  });

  function updateFilter(key: string, values: string[]) {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      next.delete(key);
      values.forEach((value) => next.append(key, value));
      next.delete("label");
      return next;
    }, { replace: true });
  }

  const filters = new URLSearchParams();
  if (q.trim()) filters.set("q", q.trim());
  if (substance.trim()) filters.set("substance", substance.trim());
  if (tradeName.trim()) filters.set("trade_name", tradeName.trim());
  if (selectedForm !== null) filters.set("forms", selectedForm);
  const filterQuery = filters.toString();
  const search = useInfiniteQuery({
    queryKey: ["kb", "drugs", filterQuery],
    queryFn: ({ pageParam }) => api.get<Page<DrugSearchItem>>(`/drugs?${filterQuery}&limit=${PAGE}&offset=${pageParam}`),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.items.length : undefined),
    placeholderData: keepPreviousData,
  });
  const items = search.data?.pages.flatMap((p) => p.items) ?? [];
  const current = selected ?? items[0]?.label_id ?? null;
  const [end, nearEnd] = useNearViewport();
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = search;
  useEffect(() => {
    if (nearEnd && hasNextPage && !isFetchingNextPage && !isFetchNextPageError) void fetchNextPage();
  }, [nearEnd, hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage]);

  return (
    <main className="sheet">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 16, flexWrap: "wrap" }}>
        <h1>Препараты и ОХЛП</h1>
        {version.data && <span className="sub">база знаний {version.data.version}</span>}
      </div>
      <div className="drug-search-fields">
        {([
          ["q", "Общий поиск", "МНН или торговое название", q],
          ["substance", "Действующее вещество (МНН)", "Введите действующее вещество", substance],
          ["trade_name", "Торговое название", "Введите торговое название", tradeName],
        ] as const).map(([key, title, placeholder, value]) => (
          <label key={key}>
            <span>{title}</span>
            <span className="search">
              <input className="big" type="search" autoComplete="off" placeholder={placeholder} value={value}
                onChange={(event) => updateFilter(key, event.target.value ? [event.target.value] : [])} />
              <SearchIcon />
            </span>
          </label>
        ))}
      </div>
      <div className="drug-filter">
        <button type="button" className="btn btn-t" aria-expanded={filterOpen} aria-controls="drug-form-filter"
          onClick={() => setFilterOpen(!filterOpen)}>
          Фильтр
        </button>
      {filterOpen && <fieldset className="drug-forms" id="drug-form-filter">
        <legend>Форма препарата</legend>
        {forms.isLoading && <Loading />}
        {forms.isError && <ErrorBox error={forms.error} onRetry={() => void forms.refetch()} />}
        {forms.data && <div className="drug-form-options">
          <label>
            <input type="checkbox" checked={allForms} onChange={() => updateFilter("forms", [])} />
            Выбрать всё
          </label>
            {forms.data.map((form) => (
              <label key={form}>
                <input type="checkbox" checked={selectedForm === form} onChange={() => updateFilter("forms", [form])} />
                {capitalize(form || "Форма не указана")}
              </label>
            ))}
        </div>}
      </fieldset>}
      </div>
      <div className="sub" role="status">
        {search.isFetching ? "Поиск…" : search.data ? `Найдено: ${search.data.pages[0]?.total ?? 0}` : ""}
      </div>
      {search.isError && !search.isFetchNextPageError && <ErrorBox error={search.error} onRetry={() => void search.refetch()} />}
      {search.isLoading && <Loading />}
      {search.isSuccess && items.length === 0 && <div className="muted">Ничего не найдено</div>}
      {items.length > 0 && (
        <div className="cols">
          <div className="list">
            {groupBySubstance(items).map((group) => (
              <section className="card" key={group.name}>
                <div className="mg">
                  <h2>{capitalize(group.name)}</h2>
                  {group.atc && <span className="sub">{group.atc}</span>}
                </div>
                {group.items.map((item) => (
                  <Link
                    key={item.label_id}
                    className={`dr${item.label_id === current ? " sel" : ""}`}
                    to={`/d/kb/drugs?${new URLSearchParams([...params.entries()].filter(([key]) => key !== "label").concat([["label", item.label_id]])).toString()}`}
                    replace
                    aria-current={item.label_id === current ? "true" : undefined}
                  >
                    <div>
                      <div style={{ fontWeight: 500 }}>{[item.trade_name, item.form].filter(Boolean).join(" · ")}</div>
                      <div className="sub">{item.substances.join(" + ")}</div>
                    </div>
                    <span className="sub atx">{item.atc ?? "—"}</span>
                  </Link>
                ))}
              </section>
            ))}
            {search.isFetchNextPageError && <ErrorBox error={search.error} onRetry={() => void search.fetchNextPage()} />}
            {search.isFetchingNextPage && <Loading />}
            <div ref={end} aria-hidden="true" />
          </div>
          {current && <Preview labelId={current} />}
        </div>
      )}
    </main>
  );
}

export function DrugLabelPage() {
  useScreen("k-drugs");
  const { labelId } = useParams();
  const label = useLabel(labelId);
  return (
    <main className="sheet">
      <Link className="sub" to="/d/kb/drugs" onClick={(event) => {
        if (window.history.length > 1) {
          event.preventDefault();
          window.history.back();
        }
      }}>
        ← Препараты и ОХЛП
      </Link>
      <Q query={label}>
        {(data) => {
          const rows = factRows(data as unknown as Record<string, unknown>);
          return (
            <section className="card">
              <LabelHead label={data} />
              {rows.map((row) => (
                <FactSection key={row.key} row={row} extra={row.key === "dose.max_text" ? maxDaily(data) : null} />
              ))}
              {typeof data.source_file === "string" && (
                <div className="row">
                  <span className="muted">Источник</span>
                  <span className="src">{data.source_file}</span>
                </div>
              )}
            </section>
          );
        }}
      </Q>
    </main>
  );
}
