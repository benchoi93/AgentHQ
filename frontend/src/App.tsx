import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  useLocation,
} from "react-router-dom";
import { lazy, Suspense } from "react";
import type { ReactNode } from "react";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import SessionDetail from "./pages/SessionDetail";

const PixelOffice = lazy(() => import("./pages/PixelOffice"));

function AuthGuard({ children }: { children: ReactNode }) {
  const location = useLocation();
  const token = localStorage.getItem("agenthq_token");
  if (!token) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return <>{children}</>;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/"
          element={
            <AuthGuard>
              <Dashboard />
            </AuthGuard>
          }
        />
        <Route
          path="/session/:id"
          element={
            <AuthGuard>
              <SessionDetail />
            </AuthGuard>
          }
        />
        <Route
          path="/pixel-office"
          element={
            <AuthGuard>
              <Suspense
                fallback={
                  <div className="h-screen flex items-center justify-center bg-slate-950 text-slate-500">
                    Loading...
                  </div>
                }
              >
                <PixelOffice />
              </Suspense>
            </AuthGuard>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
