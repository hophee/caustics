/**
 * Screen classes. Every screen's own stylesheet (src/styles/screens/*.css, generated from its canvas
 * board) is scoped as `.k.scr-<name>` / `.p.scr-<name>`; a mounted screen adds its class to the shell
 * root with `useScreen("<name>")`.
 */
import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useState, type ReactNode } from "react";

interface ScreenClasses {
  add: (name: string) => () => void;
}

const ScreenContext = createContext<ScreenClasses | null>(null);

/** Holds the classes of mounted screens; `render` gets them as a ready `className` suffix. */
export function ScreenRoot({ render }: { render: (classes: string) => ReactNode }) {
  const [names, setNames] = useState<string[]>([]);
  const add = useCallback((name: string) => {
    setNames((current) => [...current, name]);
    return () =>
      setNames((current) => {
        const index = current.indexOf(name);
        return index < 0 ? current : [...current.slice(0, index), ...current.slice(index + 1)];
      });
  }, []);
  const value = useMemo(() => ({ add }), [add]);
  const classes = [...new Set(names)].map((name) => `scr-${name}`).join(" ");
  return <ScreenContext.Provider value={value}>{render(classes)}</ScreenContext.Provider>;
}

export function useScreen(name: string): void {
  const context = useContext(ScreenContext);
  useLayoutEffect(() => context?.add(name), [context, name]);
}
