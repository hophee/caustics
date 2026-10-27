/**
 * Board K-Diagnoses: the knowledge base diagnoses (`GET /guidelines/diagnoses`) with links to the КР in
 * the Минздрав rubricator and to the care standards that cover them (D-123). A source that doesn't
 * exist for a diagnosis leaves its cell empty.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "../../../api/client";
import type { DiagnosisSources } from "../../../api/types";
import { useScreen } from "../../../ui/screen";
import { Q } from "../../../ui/states";
import { guidelineLabel, standardLabel } from "./diagnosesModel";
import "./Diagnoses.css";

export function KnowledgeDiagnoses() {
  useScreen("k-diagnoses");
  const diagnoses = useQuery({
    queryKey: ["kb", "diagnoses"],
    queryFn: () => api.get<DiagnosisSources[]>("/guidelines/diagnoses"),
    staleTime: Infinity,
  });

  return (
    <main className="sheet">
      <h1>Диагнозы</h1>
      <Q query={diagnoses}>
        {(list) => (
          <section className="card">
            <div className="scroll">
              <table>
                <thead>
                  <tr>
                    <th>Код</th>
                    <th>Диагноз</th>
                    <th>Клинические рекомендации</th>
                    <th>Стандарт медицинской помощи</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((d) => (
                    <tr key={d.code}>
                      <td className="code">{d.code}</td>
                      <td>{d.name}</td>
                      <td>
                        {d.guidelines.map((doc) => (
                          <a key={doc.id} href={doc.url} target="_blank" rel="noreferrer">
                            {guidelineLabel(doc)}
                          </a>
                        ))}
                      </td>
                      <td className="std">
                        {d.standards.map((standard) => (
                          <a key={standard.number} href={standard.url} target="_blank" rel="noreferrer" title={standard.title}>
                            {standardLabel(standard)}
                          </a>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </Q>
    </main>
  );
}
