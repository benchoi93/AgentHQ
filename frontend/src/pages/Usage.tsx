import { useEffect, useState, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import {
  BarChart3,
  Home,
  RefreshCw,
  ChevronDown,
  Zap,
  DollarSign,
  Flame,
  Server,
} from "lucide-react";
import {
  getUsageCurrent,
  getUsageHistory,
} from "../api";
import type {
  UsageCurrentResponse,
  UsageHistoryResponse,
  UsageHourlyEntry,
} from "../api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function formatNumber(n: number): string {
  return n.toLocaleString();
}

function formatCost(n: number): string {
  return `$${n.toFixed(2)}`;
}

function progressColor(pct: number): string {
  if (pct > 95) return "bg-red-500";
  if (pct > 80) return "bg-orange-500";
  if (pct > 50) return "bg-yellow-500";
  return "bg-green-500";
}

function barColor(cost: number, maxCost: number): string {
  if (maxCost === 0) return "bg-green-500";
  const ratio = cost / maxCost;
  if (ratio > 0.8) return "bg-red-500";
  if (ratio > 0.5) return "bg-yellow-500";
  return "bg-green-500";
}

function formatHourLabel(iso: string): string {
  const d = new Date(iso);
  const h = d.getHours();
  const ampm = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 || 12;
  return `${h12}${ampm}`;
}

function remainingTime(windowEnd: string): string {
  const end = new Date(windowEnd).getTime();
  const now = Date.now();
  const diff = Math.max(0, end - now);
  const hours = Math.floor(diff / 3_600_000);
  const minutes = Math.floor((diff % 3_600_000) / 60_000);
  return `${hours}h ${minutes}m remaining`;
}

function formatWindowTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZoneName: "short" });
}

// ---------------------------------------------------------------------------
// Types for range selector
// ---------------------------------------------------------------------------

type TimeRange = "24" | "48" | "168";
const RANGE_LABELS: Record<TimeRange, string> = {
  "24": "24h",
  "48": "48h",
  "168": "7d",
};

// Monthly cost limit for overuse tracking
const MONTHLY_COST_LIMIT = 200.0;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function Usage() {
  const [current, setCurrent] = useState<UsageCurrentResponse | null>(null);
  const [history, setHistory] = useState<UsageHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [range, setRange] = useState<TimeRange>("24");
  const [rangeOpen, setRangeOpen] = useState(false);
  const [machineFilter, setMachineFilter] = useState("");
  const [lastFetch, setLastFetch] = useState<number>(0);
  const [tooltipIdx, setTooltipIdx] = useState<number | null>(null);
  const navigate = useNavigate();
  const rangeRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (rangeRef.current && !rangeRef.current.contains(e.target as Node)) {
        setRangeOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const fetchData = useCallback(async () => {
    try {
      const mf = machineFilter || undefined;
      const [c, h] = await Promise.all([
        getUsageCurrent(mf),
        getUsageHistory(Number(range), mf),
      ]);
      setCurrent(c);
      setHistory(h);
      setError("");
      setLastFetch(Date.now());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch usage data");
    } finally {
      setLoading(false);
    }
  }, [range, machineFilter]);

  useEffect(() => {
    setLoading(true);
    fetchData();
    const interval = setInterval(fetchData, 10_000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // Freshness indicator — pulse for 3 seconds after fetch
  const isFresh = Date.now() - lastFetch < 3_000;

  // Hourly data for the bar chart (last N hours from range)
  const hours: UsageHourlyEntry[] = history?.hours ?? [];
  const maxHourTokens = Math.max(1, ...hours.map((h) => h.total_tokens));
  const maxHourCost = Math.max(0.001, ...hours.map((h) => h.cost_usd));

  // Model breakdown sorted by cost
  const modelEntries = current
    ? Object.entries(current.by_model).sort(
        ([, a], [, b]) => b.cost_usd - a.cost_usd,
      )
    : [];

  // Machine breakdown sorted by cost
  const machineEntries = current?.by_machine
    ? Object.entries(current.by_machine).sort(
        ([, a], [, b]) => b.cost_usd - a.cost_usd,
      )
    : [];

  // All known machine names for filter dropdown
  const machines = current?.by_machine ? Object.keys(current.by_machine).sort() : [];

  // I/O tokens (input + output, excludes cache)
  const ioTokens = current
    ? current.total_input_tokens + current.total_output_tokens
    : 0;

  // Daily cost total for monthly tracking
  const dailyCostTotal = history
    ? history.daily.reduce((sum, d) => sum + d.cost_usd, 0)
    : 0;

  return (
    <div className="min-h-screen bg-slate-950">
      {/* Header */}
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-14">
            {/* Left */}
            <div className="flex items-center gap-3">
              <BarChart3 className="w-6 h-6 text-slate-400" />
              <h1 className="text-lg font-semibold text-slate-100">Usage Monitor</h1>
              {current && (
                <span className="text-sm text-slate-500">
                  {formatCost(current.total_cost_usd)} this window
                </span>
              )}
              {/* Freshness dot */}
              <span
                className={`inline-block w-2 h-2 rounded-full transition-opacity ${
                  isFresh ? "bg-green-400 animate-pulse opacity-100" : "bg-slate-700 opacity-50"
                }`}
              />
            </div>
            {/* Right */}
            <div className="flex items-center gap-2">
              {/* Machine filter */}
              {machines.length > 1 && (
                <select
                  value={machineFilter}
                  onChange={(e) => setMachineFilter(e.target.value)}
                  className="px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-sm
                             text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">All machines</option>
                  {machines.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              )}
              {/* Time range selector */}
              <div className="relative" ref={rangeRef}>
                <button
                  onClick={() => setRangeOpen(!rangeOpen)}
                  className="flex items-center gap-1 px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-200 hover:bg-slate-700 transition-colors"
                >
                  {RANGE_LABELS[range]}
                  <ChevronDown className="w-3 h-3" />
                </button>
                {rangeOpen && (
                  <div className="absolute right-0 mt-1 bg-slate-800 border border-slate-700 rounded-lg shadow-lg overflow-hidden z-20">
                    {(Object.keys(RANGE_LABELS) as TimeRange[]).map((r) => (
                      <button
                        key={r}
                        onClick={() => {
                          setRange(r);
                          setRangeOpen(false);
                        }}
                        className={`block w-full text-left px-4 py-2 text-sm transition-colors ${
                          r === range
                            ? "bg-blue-600 text-white"
                            : "text-slate-200 hover:bg-slate-700"
                        }`}
                      >
                        {RANGE_LABELS[r]}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <button
                onClick={fetchData}
                className="p-2 text-slate-400 hover:text-slate-200 rounded-lg hover:bg-slate-800 transition-colors"
                title="Refresh"
              >
                <RefreshCw className="w-4 h-4" />
              </button>
              <button
                onClick={() => navigate("/")}
                className="p-2 text-slate-400 hover:text-slate-200 rounded-lg hover:bg-slate-800 transition-colors"
                title="Back to Dashboard"
              >
                <Home className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Session window info bar */}
      {current && (
        <div className="border-b border-slate-800/50 bg-slate-900/40">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-1.5">
            <p className="text-slate-500 text-xs">
              Current window: {formatWindowTime(current.window_start)} &ndash;{" "}
              {formatWindowTime(current.window_end)} ({remainingTime(current.window_end)})
            </p>
          </div>
        </div>
      )}

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
        {/* Loading */}
        {loading && (
          <div className="flex items-center justify-center py-20">
            <RefreshCw className="w-6 h-6 text-slate-500 animate-spin" />
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="bg-red-900/30 border border-red-800 rounded-lg p-4 text-red-300 text-sm">
            {error}
          </div>
        )}

        {!loading && !error && current && (
          <>
            {/* ── 1. Current Session Summary Cards ── */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {/* I/O Tokens */}
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Zap className="w-4 h-4 text-blue-400" />
                  <span className="text-xs text-slate-400 uppercase tracking-wider">
                    I/O Tokens
                  </span>
                </div>
                <p className="text-2xl font-bold text-blue-400">
                  {formatTokens(ioTokens)}
                </p>
                <p className="text-xs text-slate-500 mt-1">
                  In: {formatTokens(current.total_input_tokens)} &middot; Out: {formatTokens(current.total_output_tokens)}
                </p>
              </div>

              {/* Window Cost */}
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                <div className="flex items-center gap-2 mb-2">
                  <DollarSign className="w-4 h-4 text-green-400" />
                  <span className="text-xs text-slate-400 uppercase tracking-wider">
                    Window Cost
                  </span>
                </div>
                <p className="text-2xl font-bold text-green-400">
                  {formatCost(current.total_cost_usd)}
                </p>
                <p className="text-xs text-slate-500 mt-1">
                  {formatNumber(current.message_count)} messages
                </p>
              </div>

              {/* Cache Tokens */}
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Zap className="w-4 h-4 text-cyan-400" />
                  <span className="text-xs text-slate-400 uppercase tracking-wider">
                    Cache Tokens
                  </span>
                </div>
                <p className="text-2xl font-bold text-cyan-400">
                  {formatTokens(current.total_cache_read_tokens + current.total_cache_creation_tokens)}
                </p>
                <p className="text-xs text-slate-500 mt-1">
                  Write: {formatTokens(current.total_cache_creation_tokens)} &middot; Read: {formatTokens(current.total_cache_read_tokens)}
                </p>
              </div>

              {/* Burn Rate */}
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Flame className="w-4 h-4 text-amber-400" />
                  <span className="text-xs text-slate-400 uppercase tracking-wider">
                    Burn Rate
                  </span>
                </div>
                <p className="text-2xl font-bold text-amber-400">
                  {formatCost(current.burn_rate_cost_per_hour)}
                  <span className="text-sm font-normal text-slate-400">/hr</span>
                </p>
                <p className="text-xs text-slate-500 mt-1">
                  {formatTokens(current.burn_rate_tokens_per_min)} tok/min
                </p>
              </div>
            </div>

            {/* ── 2. Monthly Cost Progress ── */}
            {history && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                <h2 className="text-sm font-medium text-slate-300 mb-4">Monthly Overuse Budget</h2>
                {(() => {
                  const pct = Math.min(100, (dailyCostTotal / MONTHLY_COST_LIMIT) * 100);
                  return (
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-xs text-slate-400">
                          Overuse Spend ({range === "168" ? "7d" : range === "48" ? "2d" : "24h"} window)
                        </span>
                        <span className="text-xs text-slate-400">
                          {formatCost(dailyCostTotal)} / {formatCost(MONTHLY_COST_LIMIT)} ({pct.toFixed(1)}%)
                        </span>
                      </div>
                      <div className="bg-slate-800 rounded-full h-4">
                        <div
                          className={`${progressColor(pct)} rounded-full h-4 transition-all`}
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                  );
                })()}
              </div>
            )}

            {/* ── 3. Per-Model Breakdown Table ── */}
            {modelEntries.length > 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-5 py-4">
                  <h2 className="text-sm font-medium text-slate-300">Model Breakdown</h2>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-t border-slate-800">
                        <th className="text-left px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">
                          Model
                        </th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">
                          Input
                        </th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">
                          Output
                        </th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">
                          Cache Write
                        </th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">
                          Cache Read
                        </th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">
                          Cost
                        </th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">
                          Msgs
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {modelEntries.map(([model, data]) => (
                        <tr key={model} className="border-b border-slate-800/50">
                          <td className="px-5 py-2.5 text-slate-200 font-mono text-xs">
                            {model}
                          </td>
                          <td className="px-5 py-2.5 text-right text-slate-300 tabular-nums">
                            {formatNumber(data.input_tokens)}
                          </td>
                          <td className="px-5 py-2.5 text-right text-slate-300 tabular-nums">
                            {formatNumber(data.output_tokens)}
                          </td>
                          <td className="px-5 py-2.5 text-right text-slate-300 tabular-nums">
                            {formatNumber(data.cache_creation_tokens)}
                          </td>
                          <td className="px-5 py-2.5 text-right text-slate-300 tabular-nums">
                            {formatNumber(data.cache_read_tokens)}
                          </td>
                          <td className="px-5 py-2.5 text-right text-green-400 tabular-nums">
                            {formatCost(data.cost_usd)}
                          </td>
                          <td className="px-5 py-2.5 text-right text-slate-300 tabular-nums">
                            {formatNumber(data.message_count)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* ── 3b. Per-Machine Breakdown ── */}
            {machineEntries.length > 1 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-5 py-4">
                  <h2 className="text-sm font-medium text-slate-300 flex items-center gap-2">
                    <Server className="w-4 h-4 text-slate-400" />
                    Machine Breakdown
                  </h2>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-t border-slate-800">
                        <th className="text-left px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">Machine</th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">Input</th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">Output</th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">Cache Write</th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">Cache Read</th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">Cost</th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">Msgs</th>
                      </tr>
                    </thead>
                    <tbody>
                      {machineEntries.map(([machine, data]) => (
                        <tr key={machine} className="border-b border-slate-800/50">
                          <td className="px-5 py-2.5 text-slate-200 font-mono text-xs">{machine}</td>
                          <td className="px-5 py-2.5 text-right text-slate-300 tabular-nums">{formatNumber(data.input_tokens)}</td>
                          <td className="px-5 py-2.5 text-right text-slate-300 tabular-nums">{formatNumber(data.output_tokens)}</td>
                          <td className="px-5 py-2.5 text-right text-slate-300 tabular-nums">{formatNumber(data.cache_creation_tokens)}</td>
                          <td className="px-5 py-2.5 text-right text-slate-300 tabular-nums">{formatNumber(data.cache_read_tokens)}</td>
                          <td className="px-5 py-2.5 text-right text-green-400 tabular-nums">{formatCost(data.cost_usd)}</td>
                          <td className="px-5 py-2.5 text-right text-slate-300 tabular-nums">{formatNumber(data.message_count)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* ── 4. Hourly Usage Bar Chart ── */}
            {hours.length > 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                <h2 className="text-sm font-medium text-slate-300 mb-4">Hourly Usage</h2>
                <div className="relative">
                  {/* Bars */}
                  <div className="flex items-end gap-px h-40">
                    {hours.map((h, i) => {
                      const heightPct = (h.total_tokens / maxHourTokens) * 100;
                      return (
                        <div
                          key={h.hour}
                          className="flex-1 relative group"
                          onMouseEnter={() => setTooltipIdx(i)}
                          onMouseLeave={() => setTooltipIdx(null)}
                        >
                          <div
                            className={`w-full rounded-t ${barColor(h.cost_usd, maxHourCost)} transition-all cursor-pointer hover:opacity-80`}
                            style={{
                              height: `${Math.max(heightPct, h.total_tokens > 0 ? 2 : 0)}%`,
                            }}
                          />
                          {/* Tooltip */}
                          {tooltipIdx === i && (
                            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-xs whitespace-nowrap z-20 shadow-lg pointer-events-none">
                              <p className="text-slate-200 font-medium">{formatHourLabel(h.hour)}</p>
                              <p className="text-slate-400">
                                Tokens: {formatNumber(h.total_tokens)}
                              </p>
                              <p className="text-slate-400">
                                Cost: {formatCost(h.cost_usd)}
                              </p>
                              <p className="text-slate-400">
                                Messages: {formatNumber(h.message_count)}
                              </p>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  {/* X-axis labels — show every 6 hours */}
                  <div className="flex mt-2">
                    {hours.map((h, i) => {
                      const hourNum = new Date(h.hour).getHours();
                      const show = hourNum % 6 === 0;
                      return (
                        <div key={i} className="flex-1 text-center">
                          {show && (
                            <span className="text-xs text-slate-500">
                              {formatHourLabel(h.hour)}
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}

            {/* ── 5. Daily Summary Table ── */}
            {history && history.daily.length > 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-5 py-4">
                  <h2 className="text-sm font-medium text-slate-300">Daily Summary</h2>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-t border-slate-800">
                        <th className="text-left px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">
                          Date
                        </th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">
                          Tokens
                        </th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">
                          Input
                        </th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">
                          Output
                        </th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">
                          Cost
                        </th>
                        <th className="text-right px-5 py-2 text-slate-400 text-xs uppercase tracking-wider">
                          Messages
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {history.daily.map((d) => {
                        const isToday =
                          d.date === new Date().toISOString().slice(0, 10);
                        return (
                          <tr
                            key={d.date}
                            className={`border-b border-slate-800/50 ${
                              isToday ? "bg-blue-900/10" : ""
                            }`}
                          >
                            <td className="px-5 py-2.5 text-slate-200 font-mono text-xs">
                              {d.date}
                              {isToday && (
                                <span className="ml-2 px-1.5 py-0.5 bg-blue-900/40 text-blue-400 rounded text-xs">
                                  Today
                                </span>
                              )}
                            </td>
                            <td className="px-5 py-2.5 text-right text-slate-300 tabular-nums">
                              {formatNumber(d.total_tokens)}
                            </td>
                            <td className="px-5 py-2.5 text-right text-slate-300 tabular-nums">
                              {formatNumber(d.input_tokens)}
                            </td>
                            <td className="px-5 py-2.5 text-right text-slate-300 tabular-nums">
                              {formatNumber(d.output_tokens)}
                            </td>
                            <td className="px-5 py-2.5 text-right text-green-400 tabular-nums">
                              {formatCost(d.cost_usd)}
                            </td>
                            <td className="px-5 py-2.5 text-right text-slate-300 tabular-nums">
                              {formatNumber(d.message_count)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
