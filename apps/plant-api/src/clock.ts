/**
 * The Clock Durable Object: the only stateful thing in the plant.
 *
 *   sim_time = anchor_sim + (Date.now() - anchor_real) * speed
 *
 * The anchor pair and the speed are persisted; sim time is computed on read,
 * so no alarm has to tick every second (cheaper and exact). The DO also holds
 * the scenario overlay: current seed and injected faults, because they change
 * what every reading means and must be shared by all Worker isolates.
 */
import { DurableObject } from "cloudflare:workers";
import type { ScenarioItem } from "./sim";

export interface ClockState {
  anchorSim: number; // ms since epoch (naive UTC sim time)
  anchorReal: number; // ms since epoch (real)
  speed: number; // sim seconds per real second
}

export interface ScriptOverlay {
  seed: number;
  injected: ScenarioItem[];
}

export interface ClockEnv {
  PLANT_CLOCK_START?: string;
  PLANT_CLOCK_SPEED?: string;
}

const DEFAULT_START = Date.UTC(2024, 0, 1);

export class Clock extends DurableObject<ClockEnv> {
  private async state(): Promise<ClockState> {
    let s = await this.ctx.storage.get<ClockState>("clock");
    if (!s) {
      const start = this.env.PLANT_CLOCK_START ? Date.parse(this.env.PLANT_CLOCK_START + "Z") : DEFAULT_START;
      const speed = this.env.PLANT_CLOCK_SPEED !== undefined ? Number(this.env.PLANT_CLOCK_SPEED) : 60;
      s = { anchorSim: Number.isNaN(start) ? DEFAULT_START : start, anchorReal: Date.now(), speed };
      await this.ctx.storage.put("clock", s);
    }
    return s;
  }

  /** Current sim time (ms since epoch) and speed. */
  async now(): Promise<{ simMs: number; speed: number }> {
    const s = await this.state();
    return { simMs: s.anchorSim + (Date.now() - s.anchorReal) * s.speed, speed: s.speed };
  }

  async setSpeed(speed: number): Promise<{ simMs: number; speed: number }> {
    const cur = await this.now();
    const s: ClockState = { anchorSim: cur.simMs, anchorReal: Date.now(), speed };
    await this.ctx.storage.put("clock", s);
    return { simMs: s.anchorSim, speed };
  }

  async jump(simMs: number): Promise<{ simMs: number; speed: number }> {
    const s = await this.state();
    const next: ClockState = { anchorSim: simMs, anchorReal: Date.now(), speed: s.speed };
    await this.ctx.storage.put("clock", next);
    return { simMs, speed: s.speed };
  }

  // ----- scenario overlay ---------------------------------------------------

  async script(): Promise<ScriptOverlay> {
    return (await this.ctx.storage.get<ScriptOverlay>("script")) ?? { seed: 42, injected: [] };
  }

  async inject(item: ScenarioItem): Promise<ScriptOverlay> {
    const s = await this.script();
    s.injected.push(item);
    await this.ctx.storage.put("script", s);
    return s;
  }

  /** New seed, clear injected faults, clock back to its configured start. */
  async reset(seed: number): Promise<{ script: ScriptOverlay; clock: { simMs: number; speed: number } }> {
    const script: ScriptOverlay = { seed, injected: [] };
    await this.ctx.storage.put("script", script);
    await this.ctx.storage.delete("clock");
    const s = await this.state();
    return { script, clock: { simMs: s.anchorSim, speed: s.speed } };
  }
}
