// Shape of GET /api/run — mirrors ledgerline.web.serialize.demo_to_dict.
// Every money/quantity field is a decimal *string* (never a JS number), so the
// engine's exact figures survive the wire without float rounding.

export interface GateDecision {
  gate: string;
  verdict: string;
  reason: string | null;
  detail: Record<string, unknown>;
}

export interface GateReport {
  approved: boolean;
  decisions: GateDecision[];
}

export interface Order {
  outcome: "FILLED" | "GATE_REJECTED" | "BROKER_REJECTED";
  approved: boolean;
  filled: boolean;
  symbol: string;
  side: "BUY" | "SELL";
  quantity: string;
  limit_price: string;
  fill_price: string | null;
  broker_order_id: string | null;
  reason: string | null;
  gate_report: GateReport;
  lineage_ids: string[];
}

export interface PositionDrift {
  symbol: string;
  book_quantity: string;
  expected_quantity: string;
  quantity_drift: string;
  clean: boolean;
}

export interface Reconciliation {
  starting_cash: string;
  book_cash: string;
  expected_cash: string;
  cash_drift: string;
  clean: boolean;
  position_drifts: PositionDrift[];
}

export interface RunResult {
  orders: Order[];
  reconciliation: Reconciliation;
  lineage_intact: boolean;
  clean: boolean;
  summary: { orders: number; filled: number };
}