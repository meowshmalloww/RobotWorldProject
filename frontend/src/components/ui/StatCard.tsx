import { Icon, type IconName } from "./Icon";
import { Delta } from "./controls";
import { Sparkline } from "../charts/Sparkline";
import { DonutGauge } from "../charts/DonutGauge";
import type { Stat } from "../../data/types";

const SPARK_COLOR: Record<string, string> = {
  blue: "var(--series-1)", green: "var(--series-2)", amber: "var(--series-3)",
  purple: "var(--series-4)", teal: "var(--series-5)", red: "var(--series-6)", orange: "var(--orange)",
};

/**
 * Metric tile: top row = label + spark/donut, below = value + delta.
 * Mirrors the reference dashboard composition.
 */
export function StatCard({ stat, small }: { stat: Stat; small?: boolean }) {
  return (
    <div className="stat-card" style={{ flexDirection: "column", alignItems: "stretch", gap: 4, padding: "11px 13px" }}>
      <div className="row" style={{ gap: 7 }}>
        <span style={{ color: "var(--text-3)", display: "inline-flex", flex: "none" }}>
          <Icon name={(stat.icon as IconName) ?? "gauge"} size={14} />
        </span>
        <span className="stat-label grow">{stat.label}</span>
        {stat.spark && (
          <span style={{ flex: "none", marginTop: -2 }}>
            <Sparkline data={stat.spark} color={SPARK_COLOR[stat.tint]} width={54} height={22} />
          </span>
        )}
        {stat.donut !== undefined && (
          <span style={{ flex: "none", marginTop: -4 }}>
            <DonutGauge value={stat.donut} size={34} stroke={4} />
          </span>
        )}
      </div>
      <div className={`stat-value ${small ? "sm" : ""}`}>{stat.value}</div>
      {(stat.foot || stat.delta) && (
        <div className="stat-foot">
          {stat.delta && <Delta value={stat.delta.value} dir={stat.delta.dir} goodWhen={stat.delta.goodWhen} />}
          {stat.delta?.label && <span>{stat.delta.label}</span>}
          {!stat.delta && stat.foot && <span>{stat.foot}</span>}
        </div>
      )}
    </div>
  );
}
