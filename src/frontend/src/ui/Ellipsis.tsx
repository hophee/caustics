import "./Ellipsis.css";

/** Keep the full text available to assistive technology and the browser's hover tooltip. */
export function Ellipsis({ children }: { children: string }) {
  return <span className="text-ellipsis" title={children}>{children}</span>;
}
