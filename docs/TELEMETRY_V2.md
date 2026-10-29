# AetherTwin scene protocol 2

World coordinates are right-handed, Z up, metres. Scene timestamps retain the
acquisition common-monotonic domain; `sent_at_unix` is a separate wall clock.
Simulation clocks must never be compared directly to the browser wall clock.

`/ws/v2/scene` sends binary messages: a 12-byte little-endian `<4sHHI` prefix
(`ATWN`, version 2, flags 0, JSON header byte length), UTF-8 JSON, zero padding
to an eight-byte boundary, then contiguous typed sections. Section offsets are
relative to that data start. Each section has `name`, `dtype`, `shape`, `offset`,
and `length`. Allowed dtypes are `<f4`, `<f8`, and `<i4`. Messages are bounded
to 16 MiB, headers to 1 MiB. Reject overlapping, duplicate or out-of-bounds
sections, inconsistent shapes and unsupported versions before GPU allocation.

Header fields: `protocol_version`, `kind` (`keyframe` or `delta`), `sequence`,
`base_sequence` (null for keyframes), `stream_id`, `sent_at_unix`, `scene`, and
`sections`. `scene` is the additive v1 JSON frame with large arrays omitted.

Sections:

| Name | Type / shape | Meaning |
| --- | --- | --- |
| `voxels.keys` | i4 N×3 | Signed voxel cell coordinates, unique within a packet |
| `voxels.centers` | f4 N×3 | World positions |
| `voxels.colors` | f4 N×3 | RGB in [0,1] |
| `voxels.flow` | f4 N×3 | World scene-flow displacement in metres |
| `voxels.deleted` | i4 M×3 | Delta-only removals |
| `floor_risk.centers` | f4 G×3 | World risk sample locations |
| `floor_risk.risk` | f4 G | Relative risk |
| `floor_risk.colors` | f4 G×3 | Risk display RGB |
| `traffic_heatmap.values` | f4 H×W | Registered traffic raster |
| `path` | f8 P×3 | Validated path; drawing may convert to f4 |
| `gemini_inlay` | f4 C×6 | Bounded world XYZ/RGB samples |

Keyframes replace all voxel state. Deltas remove `deleted` keys, then upsert the
aligned key/center/color/flow rows. Other sections and small scene metadata are
complete replacements on every packet. Apply a delta only if stream identity
matches and `base_sequence` equals the last applied sequence. Otherwise request
`{"type":"resync"}` and await a keyframe. Reconnect starts with a keyframe.
If a client queue overflows, the server replaces queued messages with a current
keyframe, never an incomplete chain of deltas. Asset changes also force keyframes.

`GET /api/v2/bootstrap` returns `protocol_version`, `identity`, `source_mode`,
`hardware_status`, `map_revision`, `map_manifest_url`, `stream_url`, `role`,
`control_revision`, `capabilities`, and `latest` (v1 scene or null).
`GET /api/v2/state` returns the latest v1 scene, or `{"ready":false}`.

`POST /api/v2/commands` accepts `{request_id,map_revision,expected_revision,
action,payload}`. Actions: `pause`, `resume`, `step`, `set_start_goal`,
`set_geofences`, `seek`, `select_source`. Start/goal payload is `{start:[x,y,z],
goal:[x,y,z]}`. Geofences payload is `{geofences:[{name,center,half_extents,
yaw,severity_level}]}`. Seek payload is `{frame_index:integer}`. Source payload
is `{source:"sim"|"replay"|"live",recording_id?:string}`; only configured sources
are accepted. Responses are `{request_id,status:"accepted"|"rejected",revision,
reason}`. Accepted commands are queued; application/errors appear in command
history and subsequent scene diagnostics. Revisions prevent concurrent lost edits.

Asset manifests contain `{map_revision,coordinate_frame:"world_z_up",bounds,
tiles:[{id,bounds,lods:[{url,distance,triangles}]}]}`. GLB positions in this
application retain world Z-up coordinates as declared by the manifest; do not
apply an extra axis rotation. Render assets are never navigation authority.

Network-exposed service requires configured viewer/operator credentials; local
development may grant operator to loopback clients. Session cookies authenticate
WebSockets; credentials never enter socket URLs. Mutations check Origin when
provided. No browser command actuates a physical robot or flashes firmware.
