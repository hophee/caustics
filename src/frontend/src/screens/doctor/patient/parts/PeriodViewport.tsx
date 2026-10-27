import { useEffect, useRef, useState, type ReactNode } from "react";
import { dayMonth } from "../../../../lib/format";
import { fittingPeriod, type Period } from "./period";

export function PeriodViewport({ period, reserved = 0, children }: { period: Period; reserved?: number; children: (visible: Period) => ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const visible = fittingPeriod(period, width, reserved);
  return <div ref={ref} style={{ minWidth: 0, maxWidth: "100%" }}>
    {width > 0 && <>
      {children(visible)}
      {visible.start !== period.start && <p className="sub">Показано: {dayMonth(visible.start)} — {dayMonth(visible.end)}</p>}
    </>}
  </div>;
}
