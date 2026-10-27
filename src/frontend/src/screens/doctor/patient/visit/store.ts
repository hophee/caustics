import { useStored } from "../therapy/store";
import { emptyEncounter, type Encounter } from "./model";
const EMPTY = emptyEncounter();
const valid = (v: unknown): v is Encounter => typeof v === "object" && v !== null &&
  ["first", "repeat"].includes((v as Encounter).kind) && Array.isArray((v as Encounter).investigations);
export function useEncounter(patientId: string) {
  return useStored<Encounter>(`kompas.encounter.${patientId}`, EMPTY, valid);
}
