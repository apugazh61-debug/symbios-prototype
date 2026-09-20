# SYMBIOS — Known Limitations & Production Validation Roadmap

> [!IMPORTANT]
> **Engineering Transparency & Pre-Go-Live Validation**  
> While the software architecture, database schema, and test suites are production-grade, real-world manufacturing environments present harsh physical edge cases that must be empirically validated on-site prior to factory commissioning.

---

## 1. Known Physical & Environmental Limitations

| Factor | Challenge in Manufacturing Floor | SYMBIOS Engineering Mitigation / Validation Plan |
|---|---|---|
| **High-Visibility PPE & Bulky Garments** | Reflective vests, thermal jackets, and welding aprons alter skeletal landmark contours compared to standard civilian clothing. | Fine-tune MediaPipe/YOLO-Pose weights using on-site footage of operators wearing actual factory PPE. |
| **Industrial Dust, Fog & Lens Splatter** | Oil mist and atmospheric dust in CNC/machining areas degrade optical lens acuity. | Add automated stream clarity checks (Laplacian variance blur detection); flag degraded camera sensors automatically. |
| **Partial Occlusion by Machinery** | Machine frames, conveyor belts, and tool carts occlude the lower torso/legs. | Posture scoring relies primarily on the upper torso (shoulders, neck, spine vector) to remain resilient when legs are hidden. |
| **Industrial Camera Vibration** | Presses and heavy stamping equipment induce mechanical vibration into overhead camera mounts. | Implement digital image stabilization / Kalman-filtered landmark tracking in `tracker.py`. |

---

## 2. Simulated vs. Real Components in Current Build

### What is 100% Real & Production-Ready:
- **FastAPI Core Architecture & Asynchronous Lifespan**: Production concurrency, connection pooling, and structured JSON logs.
- **SQLAlchemy 2.0 ORM Schema & Migrations**: Multi-tenant relational schema with polygon zones and immutable version history.
- **Shapely Vector Collision Engine**: Exact 2D polygon ray-casting and proximity buffer detection.
- **Multi-Worker Spatial Tracking**: Persistent track lifecycle with velocity-based centroid matching.
- **Explainability & ISO 45001 Reports**: Zero-failure template generation and Claude API integration.
- **Security & RBAC**: Salted bcrypt password hashing and signed JWT validation.

### What is Simulated (Requires Factory Integration):
- **Cobot Hardware Drivers**: Current scheduler records and dispatches task reallocations into database records and mock webhooks. Live deployment requires connecting the `CobotScheduler` to the plant PLC / OPC-UA server or Universal Robots RTDE interface (`ur_rtde`).
- **Fatigue Training Data (v2 ML)**: Current scoring is based on calibrated heuristic kinematics (v1). Retraining the v2 ML model requires collecting 2–4 weeks of ground-truth fatigue telemetry (operator self-reports + supervisor EHS logs).

---

## 3. Recommended Factory Validation Plan (30-Day Pilot)

1. **Week 1 (Passive Calibration)**:
   - Mount RTSP cameras over 2 designated pilot workcells.
   - Run SYMBIOS in **Passive Audit Mode** (no automated cobot reassignments or audible alarms; strictly log fatigue scores and zone breaches).
2. **Week 2 (False-Positive Analysis)**:
   - Compare logged fatigue events against shift supervisor notes.
   - Adjust `FATIGUE_SLUMP_THRESHOLD_DEGREES` and `FATIGUE_STILLNESS_THRESHOLD_SECONDS` to eliminate false alarms during legitimate stationary assembly work.
3. **Week 3 (Hardware In-the-Loop Testing)**:
   - Connect `CobotScheduler` to a physical cobot in non-productive dry-run mode.
   - Validate that task reallocation latency stays under 100 ms.
4. **Week 4 (Go-Live with Human Supervision)**:
   - Enable automated safety warnings and task handover with shift supervisor active oversight.
