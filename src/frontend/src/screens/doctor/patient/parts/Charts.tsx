/** Small charts: one per diary metric (board I2-Diary) and one per scale (board I2-Scales). */
import { dayMonth } from "../../../../lib/format";
import { type Box, type ChartPoint, lines, mean, polyline, xOf, yOf } from "./chart";
import { type Period, eventTicks } from "./period";
import { score } from "./scales";
import "./Charts.css";
import { PeriodViewport } from "./PeriodViewport";

const INK = "#1d4f91";

export function HistoryChart({ period, points, label, max, formatValue, scale = false }: {
  period: Period; points: ChartPoint[]; label: string; max: number; formatValue?: (value: number) => string; scale?: boolean;
}) {
  return <div className="history-chart"><PeriodViewport period={period}>{(visible) => {
    const recent = points.filter((point) => point.day >= visible.start && point.day <= visible.end);
    return recent.length ? <>
      {scale ? <ScaleChart period={visible} points={recent} code={label} max={max} />
        : <MetricChart period={visible} points={recent} label={label} max={max} formatValue={formatValue} />}
      <Axis period={visible} eventDays={recent.map((point) => point.day)} scale={scale} />
    </> : <p className="sub">Нет данных за видимый период</p>;
  }}</PeriodViewport></div>;
}

export function Axis({ period, eventDays, scale = false }: { period: Period; eventDays: string[]; scale?: boolean }) {
  const ticks = eventTicks(period, eventDays);
  const box = scale ? SCALE_BOX : DIARY_BOX;
  return (
    <div className="event-axis">
      {ticks.map((t) => (
        <span key={t.day} style={{ left: `${100 * xOf(period, t.day, box) / box.width}%`, transform: t.col === period.days.length ? "translateX(-100%)" : t.col === 1 ? undefined : "translateX(-50%)" }}>{dayMonth(t.day)}</span>
      ))}
    </div>
  );
}

const DIARY_BOX: Box = { width: 250, height: 96, left: 8, right: 8, top: 8, bottom: 8 };

/** A diary metric: grid at zero, midpoint and maximum, dashed mean, a line broken on days without an entry. */
export function MetricChart({ period, points, label, max = 10, formatValue = String }: { period: Period; points: ChartPoint[]; label: string; max?: number; formatValue?: (value: number) => string }) {
  const box = DIARY_BOX;
  const parts = lines(period, points, 0, max, box, true);
  const avg = mean(points.map((p) => p.value));
  const last = parts.at(-1)?.at(-1);
  return (
    <svg
      className="ln"
      viewBox={`0 0 ${box.width} ${box.height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`${label} за ${dayMonth(period.start)}–${dayMonth(period.end)}: ${points.map((p) => `${dayMonth(p.day)} ${formatValue(p.value)}`).join(", ")}`}
    >
      {[0, max / 2, max].map((v) => (
        <line key={v} x1={box.left} x2={box.width - box.right} y1={yOf(v, 0, max, box)} y2={yOf(v, 0, max, box)} stroke="#efedea" strokeWidth="1" vectorEffect="non-scaling-stroke" />
      ))}
      {avg !== null && (
        <line
          x1={box.left}
          x2={box.width - box.right}
          y1={yOf(avg, 0, max, box)}
          y2={yOf(avg, 0, max, box)}
          stroke="#8a877f"
          strokeWidth="1"
          strokeDasharray="4 3"
          vectorEffect="non-scaling-stroke"
        />
      )}
      {parts.map((part) => (
        <polyline key={part[0]?.day} points={polyline(part)} fill="none" stroke={INK} strokeWidth="1.25" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      ))}
      {parts.flat().map((p) => (
        // A round, non-scaling stroke keeps the dot circular when the SVG stretches.
        <line key={p.day} x1={p.x} y1={p.y} x2={p.x} y2={p.y} stroke={INK} strokeWidth={p === last ? 6 : 3.2} strokeLinecap="round" vectorEffect="non-scaling-stroke">
          <title>{`${dayMonth(p.day)}: ${formatValue(p.value)}`}</title>
        </line>
      ))}
    </svg>
  );
}

const SCALE_BOX: Box = { width: 400, height: 140, left: 20, right: 35, top: 16, bottom: 10 };

function ticksFor(max: number): number[] {
  const step = max <= 10 ? 2 : max <= 30 ? 5 : max <= 60 ? 10 : 20;
  const out = [];
  for (let v = 0; v <= max; v += step) out.push(v);
  return out;
}

/** One scale: its own 0–max axis, every result labelled with its exact score, critical answers ringed. */
export interface TherapyOverlay { start: string; from: string; until: string; reference?: number; baseline?: number }
export function ScaleChart({ period, points, max, code, therapy }: { period: Period; points: ChartPoint[]; max: number; code: string; therapy?: TherapyOverlay }) {
  const box = SCALE_BOX;
  const [line] = lines(period, points, 0, max, box, false);
  const placed = line ?? [];
  const x = (day: string) => xOf(period, day < period.start ? period.start : day > period.end ? period.end : day, box);
  return (
    <svg
      className="sc"
      viewBox={`0 0 ${box.width} ${box.height}`}
      role="img"
      aria-label={`${code}: ${placed.map((p) => `${score(p.value)} на ${dayMonth(p.day)}`).join(", ")}`}
    >
      {ticksFor(max).map((v, i, all) => {
        const y = yOf(v, 0, max, box);
        const next = all[i + 1];
        const yNext = next === undefined ? box.top : yOf(next, 0, max, box);
        return (
          <g key={v}>
            <rect x={box.left} y={yNext} width={box.width - box.left - box.right} height={y - yNext} fill={i % 2 ? "#f8f7f4" : "#f3f1ed"} />
            <text x={box.width - box.right + 4} y={y + 3} fontSize="10" fill="#5f5d58" fontFamily="Geologica">
              {v}
            </text>
          </g>
        );
      })}
      {therapy && <g>
        {therapy.from <= period.end && therapy.until >= period.start && <rect x={x(therapy.from)} y={box.top} width={Math.max(1,x(therapy.until)-x(therapy.from))} height={box.height-box.top-box.bottom} fill="#fff0bf" opacity="0.6"><title>Срок оценки по КР</title></rect>}
        {therapy.start >= period.start && therapy.start <= period.end && <line x1={x(therapy.start)} x2={x(therapy.start)} y1={box.top} y2={box.height-box.bottom} stroke="#547ba4" strokeDasharray="3 3"><title>Начало терапии: {therapy.start}</title></line>}
        {therapy.reference !== undefined && <><line x1={box.left} x2={box.width-box.right} y1={yOf(therapy.reference,0,max,box)} y2={yOf(therapy.reference,0,max,box)} stroke="#b75e5e" strokeDasharray="5 4"/><text x={box.left+4} y={yOf(therapy.reference,0,max,box)-4} fontSize="9" fill="#994646">Критерий редукции КР</text></>}
      </g>}
      {placed.length > 1 && <polyline points={polyline(placed)} fill="none" stroke={INK} strokeWidth="2" />}
      {placed.map((p, i) => (
        <g key={`${p.day}-${i}`}>
          <circle cx={p.x} cy={p.y} r="4.5" fill={INK} stroke={p.critical ? "#f5c2c2" : undefined} strokeWidth={p.critical ? 3 : undefined} />
          <text
            x={p.x > box.width - box.right - 30 ? p.x - 8 : p.x + 8}
            y={p.y - 7}
            textAnchor={p.x > box.width - box.right - 30 ? "end" : "start"}
            fontSize="11"
            fill="#16161a"
            fontFamily="Geologica"
          >
            {score(p.value)}
          </text>
        </g>
      ))}
    </svg>
  );
}
