import { useState, type FormEvent } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Bot } from "lucide-react";

export default function Login() {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const location = useLocation();

  const from = (location.state as { from?: { pathname: string } })?.from?.pathname || "/";

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = token.trim();
    if (!trimmed) {
      setError("Token is required");
      return;
    }
    localStorage.setItem("agenthq_token", trimmed);
    navigate(from, { replace: true });
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-950 px-4">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center mb-8">
          <div className="w-16 h-16 rounded-2xl bg-slate-800 flex items-center justify-center mb-4">
            <Bot className="w-8 h-8 text-slate-300" />
          </div>
          <h1 className="text-2xl font-bold text-slate-100">AgentHQ</h1>
          <p className="text-slate-400 mt-1">AI Session Orchestrator</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor="token"
              className="block text-sm font-medium text-slate-300 mb-1.5"
            >
              API Token
            </label>
            <input
              id="token"
              type="password"
              value={token}
              onChange={(e) => {
                setToken(e.target.value);
                setError("");
              }}
              placeholder="Enter your API token"
              className="w-full px-3 py-2.5 bg-slate-800 border border-slate-700 rounded-lg
                         text-slate-100 placeholder:text-slate-500
                         focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              autoFocus
            />
            {error && (
              <p className="mt-1.5 text-sm text-red-400">{error}</p>
            )}
          </div>

          <button
            type="submit"
            className="w-full py-2.5 px-4 bg-blue-600 hover:bg-blue-500 text-white
                       font-medium rounded-lg transition-colors focus:outline-none focus:ring-2
                       focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-slate-950"
          >
            Sign In
          </button>
        </form>
      </div>
    </div>
  );
}
