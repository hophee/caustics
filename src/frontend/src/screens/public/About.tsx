/** Project page (board S-About). The fourth capability follows D-109: the patient sees no test results. */
import { Link } from "react-router";
import { CompassMark } from "../../ui/icons";
import "./public.css";

const TEAM = [
  { name: "Иван", bg: "#dfe6ef" },
  { name: "Ирина", bg: "#e9e1d3" },
  { name: "Софья", bg: "#dde8df" },
  { name: "Ясин", bg: "#ece0e2" },
];

const TRACK = [
  { label: "создан 15:47", on: true },
  { label: "отправлен 15:47", on: true },
  { label: "доставлен 15:47", on: true },
  { label: "принят 15:48", on: true },
];

export function About() {
  return (
    <div className="k ab scr-s-about">
      <div className="in">
        <header className="top">
          <Link className="mk" to="/about">
            <CompassMark size={32} />
            <b>Клинический компас</b>
          </Link>
          <nav className="tl">
            <a className="lk" href="#cap">
              Возможности
            </a>
            <a className="lk" href="#team">
              Команда
            </a>
            <Link className="btn btn-t" to="/login" style={{ height: 40, fontSize: 14 }}>
              Войти
            </Link>
          </nav>
        </header>

        <section className="hero">
          <div>
            <h1>Клинический компас</h1>
            <p className="lead">
              Помощник врача при тревожных и депрессивных расстройствах: подбор терапии по клиническим рекомендациям и инструкциям, дневник и анкеты
              пациента, сигналы безопасности.
            </p>
            <Link className="btn btn-p" to="/login">
              Войти
            </Link>
          </div>
          <div className="frag" data-ui="">
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
              <b style={{ fontWeight: 500 }}>Амитриптилин · таблетки 25 мг</b>
              <span className="lv lv-red">Противопоказано</span>
            </div>
            <div>Грудное вскармливание. Профиль пациентки: лактация — да.</div>
            <blockquote>«— период грудного вскармливания.»</blockquote>
            <div className="sub">ОХЛП Амитриптилин · раздел 4.3</div>
          </div>
        </section>

        <section id="cap">
          <div className="cap">
            <div>
              <h2>Подбор по КР и ОХЛП с основаниями</h2>
              <p>
                Варианты берутся из клинических рекомендаций для диагноза и этапа, препараты — из инструкций. У каждого предупреждения есть пункт и
                цитата. Назначение подтверждает только врач.
              </p>
            </div>
            <div className="frag" data-ui="">
              <b style={{ fontWeight: 500 }}>Антидепрессант · монотерапия</b>
              <blockquote>«…рекомендуется назначение АД с широким спектром нейрохимического механизма действия: СИОЗСН, миртазапин, ТЦА»</blockquote>
              <div className="sub">КР 301_3, п. 3.1.3 · УУР/УДД A/1</div>
            </div>
          </div>
          <div className="cap">
            <div>
              <h2>Сигналы безопасности с путём доставки</h2>
              <p>Критический ответ в анкете или дневнике сразу создаёт сигнал. Врач видит, когда сигнал создан, доставлен, принят и обработан.</p>
            </div>
            <div className="frag" data-ui="">
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <span className="lv lv-red">Срочно</span>
                <b style={{ fontWeight: 500 }}>PHQ-9, пункт 9 = 1</b>
              </div>
              <div className="trk">
                {TRACK.map((t) => (
                  <div key={t.label} className="tk">
                    <span className="bar on" />
                    {t.label}
                  </div>
                ))}
                <div className="tk" style={{ color: "#a4161a", fontWeight: 500 }}>
                  <span className="bar miss" />
                  не обработан
                </div>
              </div>
            </div>
          </div>
          <div className="cap">
            <div>
              <h2>Дневник с локальной моделью</h2>
              <p>Свободный текст дневника оценивает модель, которая работает на сервере проекта. Текст пациента не уходит во внешние сервисы.</p>
            </div>
            <div className="frag" data-ui="">
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                <b style={{ fontWeight: 500 }}>24.10, 22:10</b>
                <span className="lv lv-amb">Требует внимания</span>
              </div>
              <div>«Устала от всего. Иногда думаю, что всем было бы легче без меня.»</div>
              <div className="sub">оценка модели Qwen3-4B</div>
            </div>
          </div>
          <div className="cap">
            <div>
              <h2>Результаты анкет видит только врач</h2>
              <p>После анкеты пациент видит только, что ответы сохранены. Баллы, уровни и предупреждения остаются у врача.</p>
            </div>
            <div className="frag" data-ui="">
              <b style={{ fontWeight: 500 }}>Опросник о самочувствии · 24 октября</b>
              <div style={{ font: "500 21px/28px 'Literata',serif" }}>Ответы сохранены</div>
            </div>
          </div>
        </section>

        <section className="sec" id="team">
          <h2>Команда «Каустики»</h2>
          <div className="team">
            {TEAM.map((m) => (
              <div key={m.name} className="mem">
                <span className="ini" style={{ background: m.bg }}>
                  {m.name.charAt(0)}
                </span>
                <b>{m.name}</b>
              </div>
            ))}
          </div>
        </section>

        <footer className="foot">
          <span className="big">Клинический компас</span>
          <Link className="btn btn-t" to="/login">
            Войти
          </Link>
        </footer>
      </div>
    </div>
  );
}
