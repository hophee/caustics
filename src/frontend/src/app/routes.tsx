/** URL map. Doctor screens live under /d, patient screens under /p, public pages at the top level. */
import { Navigate, Outlet, createBrowserRouter, useLocation } from "react-router";
import type { Role } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useLive } from "../live/useLive";
import { DoctorShell } from "../shell/DoctorShell";
import { PatientShell } from "../shell/PatientShell";

import { Overview } from "../screens/doctor/Overview";
import { Patients } from "../screens/doctor/Patients";
import { KnowledgeDiagnoses } from "../screens/doctor/kb/Diagnoses";
import { DrugLabelPage, Drugs } from "../screens/doctor/kb/Drugs";
import { KnowledgeScales } from "../screens/doctor/kb/Scales";
import { PatientCard } from "../screens/doctor/patient/PatientCard";
import { Summary } from "../screens/doctor/patient/Summary";
import { Safety } from "../screens/doctor/patient/Safety";
import { DoctorDiary } from "../screens/doctor/patient/Diary";
import { DoctorScales } from "../screens/doctor/patient/Scales";
import { Therapy } from "../screens/doctor/patient/Therapy";
import { Visits } from "../screens/doctor/patient/Visits";
import { Draft } from "../screens/doctor/patient/Draft";
import { VisitWorkspace } from "../screens/doctor/patient/VisitFlow";
import { Rating } from "../screens/doctor/patient/Rating";

import { Today } from "../screens/patient/Today";
import { PatientVisits } from "../screens/patient/Visits";
import { PatientDiary } from "../screens/patient/Diary";
import { Forms } from "../screens/patient/Forms";
import { Questionnaire } from "../screens/patient/Questionnaire";
import { Meds } from "../screens/patient/Meds";
import { Help } from "../screens/patient/Help";
import { More } from "../screens/patient/More";

import { About } from "../screens/public/About";
import { Activate } from "../screens/public/Activate";
import { Login } from "../screens/public/Login";
import { Register } from "../screens/public/Register";
import { Welcome } from "../screens/account/Welcome";
import { AdminUsers } from "../screens/admin/Users";

export const HOME: Record<Role, string> = { doctor: "/d", patient: "/p", pending: "/welcome", admin: "/admin" };

/** Lets a signed-in user of `role` through; others go to the login page (or their own home). */
function RequireRole({ role }: { role: Role }) {
  const { me, loading } = useAuth();
  const location = useLocation();
  const live = role === "doctor" || role === "patient";
  useLive(live && me?.role === role ? role : undefined);
  if (loading) return <div className="boot">Загрузка…</div>;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  if (me.role !== role) return <Navigate to={HOME[me.role]} replace />;
  return <Outlet />;
}

function Home() {
  const { me, loading } = useAuth();
  if (loading) return <div className="boot">Загрузка…</div>;
  return <Navigate to={me ? HOME[me.role] : "/login"} replace />;
}

export const router = createBrowserRouter([
  { path: "/", element: <Home /> },
  { path: "/login", element: <Login /> },
  { path: "/activate", element: <Activate /> },
  { path: "/register", element: <Register /> },
  { element: <RequireRole role="pending" />, children: [{ path: "/welcome", element: <Welcome /> }] },
  { element: <RequireRole role="admin" />, children: [{ path: "/admin", element: <AdminUsers /> }] },
  { path: "/about", element: <About /> },
  {
    element: <RequireRole role="doctor" />,
    children: [
      {
        path: "/d",
        element: <DoctorShell />,
        children: [
          { index: true, element: <Overview /> },
          { path: "patients", element: <Patients /> },
          {
            path: "patients/:patientId",
            element: <PatientCard />,
            children: [
              { index: true, element: <Summary /> },
              { path: "safety", element: <Safety /> },
              { path: "diary", element: <DoctorDiary /> },
              { path: "scales", element: <DoctorScales /> },
              { path: "therapy", element: <Therapy /> },
              { path: "visits", element: <Visits /> },
            ],
          },
          { path: "patients/:patientId/draft", element: <Draft /> },
          { path: "patients/:patientId/visit", element: <VisitWorkspace /> },
          { path: "patients/:patientId/rating/:scale", element: <Rating /> },
          { path: "kb", element: <Navigate to="/d/kb/drugs" replace /> },
          { path: "kb/drugs", element: <Drugs /> },
          { path: "kb/drugs/:labelId", element: <DrugLabelPage /> },
          { path: "kb/scales", element: <KnowledgeScales /> },
          { path: "kb/diagnoses", element: <KnowledgeDiagnoses /> },
        ],
      },
    ],
  },
  {
    element: <RequireRole role="patient" />,
    children: [
      {
        path: "/p",
        element: <PatientShell />,
        children: [
          { index: true, element: <Today /> },
          { path: "diary", element: <PatientDiary /> },
          { path: "visits", element: <PatientVisits /> },
          { path: "forms", element: <Forms /> },
          { path: "forms/:assignmentId", element: <Questionnaire /> },
          { path: "meds", element: <Meds /> },
          { path: "help", element: <Help /> },
          { path: "more", element: <More /> },
        ],
      },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
