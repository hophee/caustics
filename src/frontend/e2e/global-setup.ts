import { seed } from "./seed";

/** Seeds synthetic data through the API once per run (no-op when the doctor already has patients). */
export default async function globalSetup(): Promise<void> {
  await seed();
}
