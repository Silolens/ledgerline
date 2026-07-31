"use client";

import { useEffect, useState } from "react";
import type { Order, RunResult } from "./types";

function OutcomePill({ outcome }: { outcome: Order["outcome"] }) {
  const cls = outcome.toLowerCase();
  const label = outcome.replace("_", " ");
  return <span className={`pill ${cls}`}>{label}</span>;
}

function OrdersPanel({ orders }: { orders: Order[] }) {
  if (orders.length === 0) {
    return (
      <section className="panel">
        <h2>Orders</h2>
        <p className="muted">
          No intents were routed — the signal never crossed on this scenario.
        </p>
      </section>
    );
  }
  return (
    <section className="panel">
      <h2>Orders — choke point outcomes</h2>
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th className="num">Qty</th>
            <th className="num">Limit</th>
            <th className="num">Fill</th>
            <th>Outcome</th>
            <th>Broker ID</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o, i) => (
            <tr key={o.broker_order_id ?? `${o.symbol}-${i}`}>
              <td>{o.symbol}</td>
              <td>
                <span className={`pill ${o.side.toLowerCase()}`}>{o.side}</span>
              </td>
              <td className="num">{o.quantity}</td>
              <td className="num">{o.limit_price}</td>
              <td className="num">{o.fill_price ?? "—"}</td>
              <td>
                <OutcomePill outcome={o.outcome} />
              </td>
              <td className="num muted">{o.broker_order_id ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function ReconciliationPanel({ data }: { data: RunResult["reconciliation"] }) {
  return (
    <section className="panel">
      <h2>Reconciliation — rebuilt from fills alone</h2>
      <div className="recon-grid">
        <div className="stat">
          <div className="label">Starting cash</div>
          <div className="value">{data.starting_cash}</div>
        </div>
        <div className="stat">
          <div className="label">Book cash</div>
          <div className="value">{data.book_cash}</div>
        </div>
        <div className="stat">
          <div className="label">Expected cash</div>
          <div className="value">{data.expected_cash}</div>
        </div>
        <div className="stat">
          <div className="label">Cash drift</div>
          <div className="value">{data.cash_drift}</div>
        </div>
      </div>
      {data.position_drifts.length > 0 && (
        <table style={{ marginTop: 16 }}>
          <thead>
            <tr>
              <th>Symbol</th>
              <th className="num">Book qty</th>
              <th className="num">Expected qty</th>
              <th className="num">Drift</th>
            </tr>
          </thead>
          <tbody>
            {data.position_drifts.map((d) => (
              <tr key={d.symbol}>
                <td>{d.symbol}</td>
                <td className="num">{d.book_quantity}</td>
                <td className="num">{d.expected_quantity}</td>
                <td className="num">{d.quantity_drift}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export default function Page() {
  const [data, setData] = useState<RunResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/run")
      .then((r) => {
        if (!r.ok) throw new Error(`/api/run responded ${r.status}`);
        return r.json();
      })
      .then((d: RunResult) => setData(d))
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "failed to load run")
      );
  }, []);

  return (
    <main>
      <header className="masthead">
        <h1>Ledgerline · Deterministic Run</h1>
        {data && (
          <span className={`badge ${data.clean ? "clean" : "dirty"}`}>
            {data.clean
              ? "✓ reconciled · lineage verified"
              : "✗ drift or lineage break"}
          </span>
        )}
      </header>
      <p className="subtitle">
        One fixed engine replay — no wall clock, no randomness. Every figure is
        an exact decimal string carried straight from the core.
      </p>

      {error && <div className="state error">Error: {error}</div>}
      {!data && !error && <div className="state">Running the engine…</div>}

      {data && (
        <>
          <section className="panel">
            <h2>Summary</h2>
            <div className="recon-grid">
              <div className="stat">
                <div className="label">Orders routed</div>
                <div className="value">{data.summary.orders}</div>
              </div>
              <div className="stat">
                <div className="label">Filled</div>
                <div className="value">{data.summary.filled}</div>
              </div>
              <div className="stat">
                <div className="label">Lineage</div>
                <div className="value">
                  {data.lineage_intact ? "intact" : "broken"}
                </div>
              </div>
              <div className="stat">
                <div className="label">Reconciled</div>
                <div className="value">
                  {data.reconciliation.clean ? "clean" : "drift"}
                </div>
              </div>
            </div>
          </section>

          <OrdersPanel orders={data.orders} />
          <ReconciliationPanel data={data.reconciliation} />

          <footer>
            Read-only research view. Not financial advice, not a live trading
            system. The same request always returns the same bytes.
          </footer>
        </>
      )}
    </main>
  );
}