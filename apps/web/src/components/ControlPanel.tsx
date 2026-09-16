import { useState } from "react";
import type { FuelMode, LoadState, RangeMode, SeaState } from "../types";
import { SidePanel } from "./SidePanel";

interface ControlPanelProps {
  speedKn: number;
  onSpeedKnChange: (v: number) => void;
  rpm: number | null;

  fuelMode: FuelMode;
  fuelValue: number;
  onFuelChange: (mode: FuelMode, value: number) => void;

  reservePct: number;
  onReservePctChange: (v: number) => void;

  seaState: SeaState;
  onSeaStateChange: (v: SeaState) => void;

  loadState: LoadState;
  onLoadStateChange: (v: LoadState) => void;

  clearanceM: number;
  onClearanceMChange: (v: number) => void;

  rangeMode: RangeMode;
  onRangeModeChange: (v: RangeMode) => void;

  tankCapacityL: number;
  speedMinKn: number;
  speedMaxKn: number;
  developerMode: boolean;
  onDeveloperModeChange: (v: boolean) => void;
}

const SEA_STATES: SeaState[] = ["calm", "moderate", "rough"];
const SEA_STATE_LABELS: Record<SeaState, string> = { calm: "Calm", moderate: "Moderate", rough: "Rough" };

const LOAD_STATES: LoadState[] = ["light", "normal", "heavy"];
const LOAD_STATE_LABELS: Record<LoadState, string> = { light: "Light", normal: "Normal", heavy: "Heavy" };

const CLEARANCE_STEPS = [25, 50, 100, 250];

export function ControlPanel(props: ControlPanelProps) {
  const [performanceOpen, setPerformanceOpen] = useState(false);
  const [planningOpen, setPlanningOpen] = useState(false);
  return (
    <div className="control-stack">
      <SidePanel id="performance" kicker="02 · Performance" title="Cruising settings" open={performanceOpen} onOpenChange={setPerformanceOpen}>
        <div className="field-group">
        <label className="field-label" htmlFor="speed">Speed</label>
        <div className="field-row">
          <input
            id="speed"
            type="range"
            min={props.speedMinKn}
            max={props.speedMaxKn}
            step={0.5}
            value={props.speedKn}
            onChange={(e) => props.onSpeedKnChange(parseFloat(e.target.value))}
          />
          <span className="value">{props.speedKn.toFixed(1)} kn</span>
        </div>
        <span className="field-row" style={{ fontSize: "0.68rem", color: "var(--ink-dim)" }}>
          Calibrated range for this boat: {props.speedMinKn.toFixed(0)}-{props.speedMaxKn.toFixed(0)} kn
        </span>
        {props.rpm != null && (
          <div className="field-row">
            <span className="field-label" style={{ textTransform: "none", letterSpacing: 0 }}>
              Engine speed
            </span>
            <span className="value">{Math.round(props.rpm)} RPM</span>
          </div>
        )}
        </div>

        <div className="field-group">
          <span className="field-label">Sea state</span>
          <div className="segmented">
            {SEA_STATES.map((s) => (
              <button key={s} className={s === props.seaState ? "active" : ""} onClick={() => props.onSeaStateChange(s)} type="button">
                {SEA_STATE_LABELS[s]}
              </button>
            ))}
          </div>
        </div>

        <div className="field-group">
          <span className="field-label">Load</span>
          <div className="segmented">
            {LOAD_STATES.map((s) => (
              <button key={s} className={s === props.loadState ? "active" : ""} onClick={() => props.onLoadStateChange(s)} type="button">
                {LOAD_STATE_LABELS[s]}
              </button>
            ))}
          </div>
        </div>
      </SidePanel>

      <SidePanel id="planning" kicker="03 · Planning" title="Fuel & safety" open={planningOpen} onOpenChange={setPlanningOpen}>
        <div className="field-group">
          <label className="field-label" htmlFor="fuel">Fuel</label>
        <div className="field-row">
          <select
            id="fuel-mode"
            value={props.fuelMode}
            onChange={(e) => props.onFuelChange(e.target.value as FuelMode, props.fuelValue)}
          >
            <option value="percent">Percentage</option>
            <option value="liters">Liters</option>
            <option value="full">Full tank</option>
          </select>
          {props.fuelMode !== "full" && (
            <input
              type="number"
              min={0}
              max={props.fuelMode === "percent" ? 100 : props.tankCapacityL}
              value={props.fuelValue}
              onChange={(e) => props.onFuelChange(props.fuelMode, parseFloat(e.target.value) || 0)}
              style={{ width: "5.5rem" }}
            />
          )}
        </div>
        {props.fuelMode === "percent" && (
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={props.fuelValue}
            onChange={(e) => props.onFuelChange("percent", parseFloat(e.target.value))}
          />
        )}
        </div>

        <div className="field-group">
        <label className="field-label" htmlFor="reserve">Fuel reserve</label>
        <div className="field-row">
          <input
            id="reserve"
            type="range"
            min={0}
            max={50}
            step={1}
            value={props.reservePct}
            onChange={(e) => props.onReservePctChange(parseFloat(e.target.value))}
          />
          <span className="value">%{props.reservePct}</span>
        </div>
        </div>

        <div className="field-group">
        <span className="field-label">Safety clearance</span>
        <div className="segmented">
          {CLEARANCE_STEPS.map((c) => (
            <button
              key={c}
              className={c === props.clearanceM ? "active" : ""}
              onClick={() => props.onClearanceMChange(c)}
              type="button"
            >
              {c}m
            </button>
          ))}
        </div>
        </div>

        <div className="field-group">
        <span className="field-label">Range mode</span>
        <div className="segmented">
          <button
            className={props.rangeMode === "one_way" ? "active" : ""}
            onClick={() => props.onRangeModeChange("one_way")}
            type="button"
          >
            One way
          </button>
          <button
            className={props.rangeMode === "round_trip" ? "active" : ""}
            onClick={() => props.onRangeModeChange("round_trip")}
            type="button"
          >
            Round trip
          </button>
        </div>
        </div>

        <div className="field-group developer-toggle">
        <label className="field-row" htmlFor="developer-mode">
          <input
            id="developer-mode"
            type="checkbox"
            checked={props.developerMode}
            onChange={(e) => props.onDeveloperModeChange(e.target.checked)}
          />
          <span>Developer diagnostics</span>
        </label>
        </div>
      </SidePanel>
    </div>
  );
}
