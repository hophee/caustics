/** Full-screen steps (diary, questionnaire) and the «saved» screen after them (board P-Result). */
import type { ReactNode } from "react";
import { Link } from "react-router";
import { useScreen } from "../../ui/screen";
import { CloseIcon, DoneIcon } from "./MoodFace";
import "./patient.css";

/** A step screen: the board's close button instead of the section navigation. */
export function FlowScreen({ screen, closeLabel, closeTo = "/p", children }: { screen: string; closeLabel: string; closeTo?: string; children: ReactNode }) {
  useScreen(screen);
  useScreen("p-flow");
  return (
    <main className="wrap narrow">
      <Link to={closeTo} className="btn btn-s flow-close" aria-label={closeLabel}>
        <CloseIcon />
      </Link>
      {children}
    </main>
  );
}

/** Board P-Result: only that the answers are saved and the way home — never a score or a result (D-109). */
export function SavedScreen({ caption, title }: { caption: string; title: string }) {
  useScreen("p-result");
  return (
    <main className="wrap narrow">
      <section className="card" style={{ display: "flex", flexDirection: "column", gap: 16, padding: "28px 24px" }}>
        <span className="done">
          <DoneIcon />
        </span>
        <div>
          <div className="sub">{caption}</div>
          <h1 style={{ marginTop: 4 }}>{title}</h1>
        </div>
      </section>
      <div className="acts">
        <Link className="btn btn-p" to="/p">
          Вернуться на главную
        </Link>
      </div>
    </main>
  );
}
