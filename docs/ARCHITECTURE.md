# SYMBIOS — System Architecture & Scaling Specification

## 1. System Topology & Data Flow

SYMBIOS employs a decoupled event-driven architecture designed for high-throughput, low-latency industrial safety monitoring.

```
[Camera Streams (RTSP/WebRTC)]
           │ (Downsampled 3-5 FPS)
           ▼
[Ingestion Gateway]
           │
           ▼ (Raw Frame Payloads)
[Redis Stream: "raw_frames:{camera_id}"]
           │
           ▼ (Consumer Group)
[Stateless Perception Workers (Docker/K8s)]
  ├── MediaPipe Pose Landmark Extraction
  ├── ByteTrack Multi-Person Centroid Re-ID
  ├── Kinematic Fatigue Feature Extraction
  └── [GDPR PURGE: Immediate Frame Byte Deletion]
           │
           ▼ (Extracted Keypoint Telemetry)
[Redis Pub/Sub: "telemetry_events"]
           │
           ▼
[Decision & Safety Orchestration Service]
  ├── Shapely Vector Polygon Intersection Check
  ├── Rule-Based Matrix (v1) / RL Policy Inference (v2)
  └── Cobot Availability & Task Scheduling Queue
           │
     ┌─────┴─────────────────────────┐
     ▼                               ▼
[Immutable Database Audit]      [Real-Time Dispatcher]
  ├── PostgreSQL 16 (PostGIS)     ├── WebSocket Push to Dashboards
  ├── DecisionLogs                ├── OPC-UA / MQTT to Cobot Controller
  └── SafetyAlerts                └── Slack / Twilio Escalation Webhooks
```

---

## 2. Horizontal Scaling Strategy (50+ Feeds, 200+ Workers)

In an enterprise manufacturing environment, running computer vision on 50+ camera streams simultaneously cannot be handled by a single server.

### A. Frame Decoupling via Redis Streams
- Camera feeds are partitioned into streams: `frames:cell-01`, `frames:cell-02`, etc.
- Ingestion services perform frame dropping if the consumer queue backs up, ensuring that safety decisions are **never evaluated on stale footage** (Maximum allowable queue delay: $\Delta t < 250\text{ms}$).

### B. Scalable Perception Pods
- Perception workers are containerized and stateless.
- Each worker pod processes 3–5 camera feeds (consuming ~1.2 CPU cores per feed with MediaPipe Lite/Normal).
- Kubernetes Horizontal Pod Autoscaler (HPA) scales pods dynamically based on CPU utilization and Redis stream backlog.

### C. Latency Budget Analysis
To achieve real-time preemption before human-robot physical impact occurs:
- **Frame Ingestion & Transport**: 20–35 ms
- **Pose Extraction (CPU MediaPipe)**: 25–40 ms
- **Polygon Collision & Decision Engine**: < 5 ms
- **Cobot Dispatch / E-Stop Webhook**: 10–15 ms
- **Total Pipeline Latency**: **60–95 ms** (well within the standard OSHA 250 ms human reaction threshold).

---

## 3. Database Schema & Multi-Tenancy

- **PostgreSQL 16 + PostGIS**: Handles spatial polygon indexing, spatial bounding queries (`ST_Contains`, `ST_Distance`), and relational entities.
- **Multi-Tenancy**: Hard site-level partitioning (`company_id`, `site_id`) ensuring data isolation between different manufacturing plants and business units.
- **Audit Immutability**: All records in `decision_logs`, `safety_zone_history`, and `incident_explanations` are append-only.
