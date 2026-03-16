import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  useLocation,
} from "react-router-dom";
import type { ReactNode } from "react";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import SessionDetail from "./pages/SessionDetail";

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
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
