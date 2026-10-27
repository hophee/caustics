/** «Нужна помощь?» (board P-Help): emergency number. No messages to the doctor (D-120). */
import { useScreen } from "../../ui/screen";
import "./patient.css";

function PhoneIcon({ stroke }: { stroke: string }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth="1.8" aria-hidden="true">
      <path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2" />
    </svg>
  );
}

export function Help() {
  useScreen("p-help");
  return (
    <main className="wrap narrow">
      <h1>Если вам сейчас тяжело</h1>
      <div className="list">
        <a className="call main" href="tel:112">
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <span className="ico">
              <PhoneIcon stroke="#fff" />
            </span>
            <div>
              <div className="nm">Экстренные службы</div>
              <div className="s">круглосуточно</div>
            </div>
          </div>
          <span className="num">112</span>
        </a>
      </div>
    </main>
  );
}
