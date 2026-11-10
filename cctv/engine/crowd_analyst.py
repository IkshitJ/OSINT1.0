"""
Crowd Dynamics & Stampede Prevention analysis.

Fuses the live Headcounter metrics (density, tripwire flow, trend) with
upstream GTFS-Realtime feeds and asks Claude for a 20-minute density forecast,
risk state, countermeasure playbook and a machine-readable actuation payload.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional, Literal

import anthropic
import requests
from pydantic import BaseModel, ConfigDict, Field

from cctv.config import (
    ANALYSIS_MODEL,
    ANALYSIS_EFFORT,
    ZONE_NAME,
    ZONE_AREA_M2,
    ZONE_TOPOLOGY,
    DENSITY_CRITICAL,
    DENSITY_FATAL,
    FLOW_WINDOW_SECONDS,
    GTFS_RT_TRIP_UPDATES_URL,
    GTFS_RT_VEHICLE_POSITIONS_URL,
    GTFS_STOP_IDS,
    GTFS_LOOKAHEAD_MINUTES,
    GTFS_DEFAULT_ALIGHTING_PER_ARRIVAL,
)

logger = logging.getLogger("CrowdAnalyst")


# =============================================================================
# Output schema (structured output - the model must fill exactly this shape)
# =============================================================================
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrajectoryPoint(_Strict):
    horizon_min: int = Field(description="Forecast horizon in minutes: 5, 10, 15 or 20")
    density_pax_m2: float
    flow_velocity_ms: float
    los: Literal["A", "B", "C", "D", "E", "F"]
    surge_probability_pct: float = Field(ge=0, le=100)


class SensorSynthesis(_Strict):
    active_bottlenecks: List[str]
    inflow_per_min: float = Field(description="Lambda - persons/minute entering the zone")
    outflow_per_min: float = Field(description="Mu - persons/minute leaving the zone")
    sensor_concordance: str = Field(description="Agreement/anomalies between CCTV, GTFS and other inputs")


class ThreatAssessment(_Strict):
    risk_level: Literal["NORMAL", "ADVISORY", "CRITICAL", "SURGE_IMMINENT"]
    primary_failure_mode: str
    minutes_to_critical: Optional[float] = Field(
        description="Minutes until density reaches the critical threshold, or null if not expected within 20 min"
    )


class Playbook(_Strict):
    t0_to_3_min: List[str]
    t3_to_10_min: List[str]
    t10_to_20_min: List[str]


class ActuationFlags(_Strict):
    trigger_pa_announcement: bool
    update_digital_signage: bool
    halt_inbound_escalators: bool
    meter_turnstiles: bool
    divert_to_bypass: bool
    hold_upstream_trains: bool
    close_platform_stairs: bool


class CrowdAssessment(_Strict):
    synthesis: SensorSynthesis
    trajectory: List[TrajectoryPoint]
    threat: ThreatAssessment
    playbook: Playbook
    actuation: ActuationFlags
    confidence: float = Field(ge=0, le=1, description="Confidence in the forecast given the inputs available")
    missing_inputs: List[str] = Field(description="Sensor modalities that were unavailable for this assessment")
    assumptions: List[str]
    markdown_report: str = Field(description="Sections 1-4 of the operator report in Markdown")


# =============================================================================
# System prompt (stable - cached across requests)
# =============================================================================
SYSTEM_PROMPT = f"""You are the Real-Time Crowd Dynamics & Stampede Prevention Engine (CD-SPE). You ingest multi-modal sensor telemetry at physical choke points (railway stations, temples, stadiums, clubs, festival grounds) and forecast crowd density up to 20 minutes ahead to prevent crowd collapse, turbulence and crush asphyxia.

<crowd_physics_principles>
Adhere to pedestrian dynamics and John J. Fruin's Level of Service (LOS) standards:
- Conservation of crowd flow: dN/dt = inflow rate (lambda) - outflow rate (mu).
- Free flow: < 1.5 people/m^2 (LOS A-C).
- Constrained flow: 2.0 - 3.5 people/m^2 (LOS D-E; walking speed drops below 0.7 m/s).
- Critical threshold: {DENSITY_CRITICAL} people/m^2 (LOS F; body contact, stop-and-go shockwaves).
- Fatal / turbulent zone: >= {DENSITY_FATAL} people/m^2 (progressive crowd collapse, compressive asphyxiation, lateral shockwaves).
- Shockwave velocity: in dense crowds pressure waves travel backwards at ~1.0 - 1.5 m/s.
</crowd_physics_principles>

<sensor_telemetry_modalities>
Correlate and cross-calibrate across whichever of these streams are present:
1. Optical flow & vision (edge CCTV): headcount, ROI density, tripwire IN/OUT rates, trend/speed decay.
2. Wireless & RF probing: Wi-Fi probe rate, BLE beacons, cellular sector handover deltas (15-30 min lead).
3. Transit & upstream feeds (GTFS-RT): inbound arrivals, delays (simultaneous delayed arrivals = compound pulse), vehicle occupancy, gate tap rates.
4. Physical topology: corridor width reductions, stairs, escalators, turnstiles, gate states, one-way vs two-way paths.
5. Environmental & temporal drivers: weather, event milestones, acoustic spikes.
</sensor_telemetry_modalities>

<task>
Given the telemetry JSON for a zone:
1. Fuse the sensors. Weight and reconcile conflicting inputs. State plainly which modalities are missing and lower `confidence` accordingly - never invent readings for sensors that were not supplied.
2. Model crowd momentum: lambda vs mu over time, including upstream pulses from GTFS arrivals (use the supplied alighting assumption when occupancy is absent).
3. Forecast density, flow velocity, LOS and surge probability at T+5, T+10, T+15 and T+20 minutes.
4. Determine the risk state: NORMAL | ADVISORY | CRITICAL | SURGE_IMMINENT.
5. Issue concrete, prioritized countermeasures (metering, gate diversions, upstream train holding, escalator reversal) and set the actuation flags consistently with the playbook.
</task>

<markdown_report_format>
Write `markdown_report` with exactly these sections (the JSON payload is attached separately by the server):

### 1. Spatial & Sensor Synthesis
- **Active Bottlenecks Identified**: ...
- **Inflow vs. Outflow Dynamics**: current lambda vs mu in persons/minute
- **Sensor Concordance / Anomalies**: ...

### 2. 20-Minute Predictive Trajectory
| Horizon | Predicted Density (pax/m²) | Flow Velocity (m/s) | LOS Level | Surge Probability (%) |
|:---|:---|:---|:---|:---|
| T + 5 min | ... | ... | ... | ...% |
| T + 10 min | ... | ... | ... | ...% |
| T + 15 min | ... | ... | ... | ...% |
| T + 20 min | ... | ... | ... | ...% |

### 3. Threat Assessment
- **Risk Level**: ...
- **Primary Failure Mode**: ...
- **Time to Critical Density (>= {DENSITY_CRITICAL} pax/m²)**: X minutes or "N/A"
- **Confidence / Missing Inputs**: ...

### 4. Immediate Automated & Human Actions (0-20 min Playbook)
- **T + 0 to T + 3 min (Automated / Rapid Intervention)**: ...
- **T + 3 to T + 10 min (Flow Modulation)**: ...
- **T + 10 to T + 20 min (Upstream Throttle)**: ...
</markdown_report_format>
"""


# =============================================================================
# Telemetry collection
# =============================================================================
def collect_telemetry(pipeline, area_m2: float, window_s: int = FLOW_WINDOW_SECONDS) -> Dict[str, Any]:
    """Builds the CCTV telemetry block from the live pipeline state."""
    metrics = dict(pipeline.current_metrics)
    history: List[Dict[str, Any]] = list(pipeline.counter.history)
    now = time.time()

    inflow_per_min = outflow_per_min = 0.0
    roi_trend_per_min = 0.0
    window_used_s = 0.0
    if len(history) >= 2:
        recent = [h for h in history if now - h["time"] <= window_s]
        if len(recent) < 2:
            recent = history[-2:]
        first, last = recent[0], recent[-1]
        window_used_s = max(last["time"] - first["time"], 1e-6)
        scale = 60.0 / window_used_s
        inflow_per_min = (last["in"] - first["in"]) * scale
        outflow_per_min = (last["out"] - first["out"]) * scale
        roi_trend_per_min = (last["roi_count"] - first["roi_count"]) * scale

    roi_count = metrics.get("roi_head_count", 0)
    return {
        "captured_at_unix": now,
        "source": str(pipeline.source),
        "is_paused": pipeline.is_paused,
        "detector": {
            "model": pipeline.detector.model_name,
            "conf_threshold": pipeline.conf_threshold,
            "fps": metrics.get("fps", 0.0),
        },
        "counts": {
            "frame_head_count": metrics.get("current_head_count", 0),
            "roi_head_count": roi_count,
            "peak_head_count": metrics.get("peak_head_count", 0),
            "total_unique_heads": len(pipeline.counter.seen_track_ids),
            "in_count_total": metrics.get("in_count", 0),
            "out_count_total": metrics.get("out_count", 0),
        },
        "density": {
            "area_m2": area_m2,
            "roi_density_pax_m2": round(roi_count / area_m2, 3) if area_m2 > 0 else None,
        },
        "flow": {
            "window_seconds": round(window_used_s, 1),
            "inflow_per_min": round(inflow_per_min, 2),
            "outflow_per_min": round(outflow_per_min, 2),
            "roi_count_trend_per_min": round(roi_trend_per_min, 2),
            "tripwire_enabled": pipeline.counter.counting_line is not None,
            "roi_enabled": bool(pipeline.counter.roi_polygon),
        },
        "history_samples": [
            {"t_minus_s": round(now - h["time"], 1), "count": h["count"], "roi": h["roi_count"]}
            for h in history[-24:]
        ],
    }


def fetch_gtfs_rt(stop_ids: List[str], lookahead_min: int = GTFS_LOOKAHEAD_MINUTES) -> Dict[str, Any]:
    """Pulls upstream arrivals (TripUpdates) and occupancy (VehiclePositions) for the zone's stops."""
    if not GTFS_RT_TRIP_UPDATES_URL and not GTFS_RT_VEHICLE_POSITIONS_URL:
        return {"available": False, "reason": "No GTFS-RT feed configured"}
    if not stop_ids:
        return {"available": False, "reason": "GTFS_STOP_IDS is empty - no stops map to this zone"}

    from google.transit import gtfs_realtime_pb2  # imported lazily: optional dependency

    now = time.time()
    horizon = now + lookahead_min * 60
    result: Dict[str, Any] = {"available": True, "stop_ids": stop_ids, "arrivals": [], "vehicles": []}
    stop_set = set(stop_ids)

    def _load(url: str):
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(resp.content)
        return feed

    try:
        if GTFS_RT_TRIP_UPDATES_URL:
            feed = _load(GTFS_RT_TRIP_UPDATES_URL)
            for ent in feed.entity:
                if not ent.HasField("trip_update"):
                    continue
                tu = ent.trip_update
                for stu in tu.stop_time_update:
                    if stu.stop_id not in stop_set:
                        continue
                    ev = stu.arrival if stu.HasField("arrival") else stu.departure
                    if not ev.time or not (now - 60 <= ev.time <= horizon):
                        continue
                    result["arrivals"].append({
                        "trip_id": tu.trip.trip_id,
                        "route_id": tu.trip.route_id,
                        "stop_id": stu.stop_id,
                        "minutes_out": round((ev.time - now) / 60, 1),
                        "delay_s": ev.delay,
                        "schedule_relationship": tu.trip.ScheduleRelationship.Name(tu.trip.schedule_relationship),
                    })
            result["arrivals"].sort(key=lambda a: a["minutes_out"])

        if GTFS_RT_VEHICLE_POSITIONS_URL:
            feed = _load(GTFS_RT_VEHICLE_POSITIONS_URL)
            arriving_trips = {a["trip_id"] for a in result["arrivals"]}
            for ent in feed.entity:
                if not ent.HasField("vehicle"):
                    continue
                vp = ent.vehicle
                if arriving_trips and vp.trip.trip_id not in arriving_trips:
                    continue
                result["vehicles"].append({
                    "trip_id": vp.trip.trip_id,
                    "occupancy_status": vp.OccupancyStatus.Name(vp.occupancy_status) if vp.HasField("occupancy_status") else None,
                    "occupancy_percentage": vp.occupancy_percentage if vp.HasField("occupancy_percentage") else None,
                    "current_status": vp.VehicleStopStatus.Name(vp.current_status),
                })
    except Exception as exc:  # feed down, bad key, malformed protobuf
        logger.warning("GTFS-RT fetch failed: %s", exc)
        result["available"] = False
        result["reason"] = f"GTFS-RT fetch failed: {exc}"

    result["assumed_alighting_per_arrival"] = GTFS_DEFAULT_ALIGHTING_PER_ARRIVAL
    return result


# =============================================================================
# Analysis
# =============================================================================
_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def analyze(
    pipeline,
    zone_name: str = ZONE_NAME,
    area_m2: float = ZONE_AREA_M2,
    topology: str = ZONE_TOPOLOGY,
    operator_notes: Optional[str] = None,
    extra_telemetry: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Runs one CD-SPE assessment. Returns {"telemetry", "assessment", "report_markdown", "payload", "usage"}.
    Raises anthropic.* errors to the caller; the endpoint maps them to HTTP statuses.
    """
    telemetry = {
        "zone": {"name": zone_name, "area_m2": area_m2, "topology": topology},
        "thresholds": {"critical_pax_m2": DENSITY_CRITICAL, "fatal_pax_m2": DENSITY_FATAL},
        "cctv": collect_telemetry(pipeline, area_m2),
        "gtfs_rt": fetch_gtfs_rt(GTFS_STOP_IDS),
        "rf_probes": None,
        "acoustic": None,
        "environment": None,
        "operator_notes": operator_notes,
    }
    if extra_telemetry:
        telemetry.update(extra_telemetry)

    client = _get_client()
    response = client.beta.messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=16000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{
            "role": "user",
            "content": (
                "Current telemetry snapshot for the zone (JSON). Fields that are null were not "
                "supplied by any sensor.\n\n" + json.dumps(telemetry, indent=2, sort_keys=True)
            ),
        }],
        thinking={"type": "adaptive"},
        output_config={
            "effort": ANALYSIS_EFFORT,
            "format": {"type": "json_schema", "schema": CrowdAssessment.model_json_schema()},
        },
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )

    if response.stop_reason == "refusal":
        category = response.stop_details.category if response.stop_details else None
        raise RuntimeError(f"Model declined the request (category={category})")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Model output truncated at max_tokens")

    text = next(b.text for b in response.content if b.type == "text")
    assessment = CrowdAssessment.model_validate_json(text)

    payload = {
        "generated_at_unix": time.time(),
        "zone": zone_name,
        "model": response.model,
        "risk_level": assessment.threat.risk_level,
        "primary_failure_mode": assessment.threat.primary_failure_mode,
        "minutes_to_critical": assessment.threat.minutes_to_critical,
        "current_density_pax_m2": telemetry["cctv"]["density"]["roi_density_pax_m2"],
        "inflow_per_min": assessment.synthesis.inflow_per_min,
        "outflow_per_min": assessment.synthesis.outflow_per_min,
        "forecast": [p.model_dump() for p in assessment.trajectory],
        "actuation": assessment.actuation.model_dump(),
        "confidence": assessment.confidence,
        "missing_inputs": assessment.missing_inputs,
    }

    return {
        "telemetry": telemetry,
        "assessment": assessment.model_dump(),
        "report_markdown": assessment.markdown_report
        + "\n\n### 5. Telemetry JSON Payload\n```json\n"
        + json.dumps(payload, indent=2)
        + "\n```\n",
        "payload": payload,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_read_input_tokens": response.usage.cache_read_input_tokens,
        },
    }
