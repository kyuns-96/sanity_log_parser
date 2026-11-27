# `di_design_artifact` — Floorplan · 스크린샷 전용 테이블 설계 및 신청 안내

블록·버전(`NET_VER · REVISION · ECO_NUM`)별 Floorplan, Placement, Congestion, IR-drop, Power Map, 기타 설계 스크린샷을 저장하고, 기존 Sanity(`sanity_oa_data`) 및 Area/Power(`di_area_power_*`)와 조인해 활용하기 위한 전용 테이블입니다.

## 1. 신청 및 적용 순서

1. **사전 확인**: [00_inspect.sql](00_inspect.sql)의 `<schema>`를 실제 스키마로 치환해 실행(읽기 전용).
2. **테이블 생성 신청**: 사내 DB 신청 포털의 컬럼 정의란에 [01_di_design_artifact.columns.sql](01_di_design_artifact.columns.sql) 붙여넣기(바깥 `CREATE TABLE` 없음, 테이블명 `di_design_artifact`).
3. **제약·TOAST 스토리지 적용**: 테이블 생성 완료 후 [02_constraints.sql](02_constraints.sql) 실행.
   - `artifact_bytes`·`thumbnail_bytes`에 `STORAGE EXTERNAL`을 적용해 이미 압축된 PNG/WebP의 중복 압축 CPU 낭비를 막고 본 테이블 스캔과 물리 분리합니다.
   - 단일 파일 상한 **16 MiB**, 썸네일 상한 **256 KiB**, DB 레벨 `sha256(artifact_bytes)` 무결성 검증을 포함합니다.
4. **인덱스 적용**: [03_indexes.sql](03_indexes.sql) 실행(중복 업로드 방지용 부분 UNIQUE 인덱스 포함).
5. **권한 부여**: OA/VWP 앱 계정에 `SELECT, INSERT, UPDATE, DELETE` 부여(단일 계정 모드는 기존 공용 계정에 동일 부여).

## 2. 컬럼 구조 및 설계 의도

| 구분 | 컬럼 | 용도 |
|---|---|---|
| **OA 공통 식별 키** | `project_id`, `block_id`, `version_id` | `sanity_oa_data`의 `WS_PROJECT / WS_BLOCK / WS_VERSION` `workspace_key`와 1:1 매칭 (`version_id` NULL 허용으로 블록 공통 자료도 지원) |
| **설계 버전 비즈니스 키** | `net_ver`, `revision`, `eco_num`, `layout` | JSON 파싱 없이 버전별 타임라인·ECO 추이 조회 및 VWP 자동 수집 시 직접 기록 |
| **Area/Power · Sanity 브리지 키** | `area_project_id`, `area_block_id`, `area_version_id`, `area_snapshot_id`, `area_upload_id`, `sanity_report_id` | Area/Power 비교·특정 PTPX 코너 업로드·특정 Sanity 보고서와 선택적 1:1 조인(크로스 테이블 하드 FK 없이 독립 수명주기 유지) |
| **분류·조건 메타** | `artifact_kind`, `design_stage`, `tool_name`, `view_name`, `process_label`, `voltage_v`, `temp_c`, `pex` | `floorplan / placement / congestion / clock_tree / routing / power_map / ir_drop / timing_map / density_map / drc_map / screenshot / other` 구분. Floorplan은 코너 NULL, IR-drop/Power map은 코너 기입 |
| **설명·확장 메타** | `title`, `caption`, `tags` (JSONB 배열), `metadata` (JSONB 객체) | 검색 태그, 툴 버전·좌표/스케일(오버레이 정렬용), 추후 VLM(이미지 AI) 요약·주석 확장 |
| **바이너리·무결성** | `original_filename`, `mime_type`, `width_px`, `height_px`, `sha256`, `byte_length`, `artifact_bytes`, `thumbnail_bytes` | 목록 조회 시 `artifact_bytes` 제외해 고속 조회, 단건 조회 시 `ETag=sha256` + `Cache-Control: immutable` 캐시, `thumbnail_bytes`로 갤러리 즉시 로딩 |
| **상태·감사** | `state` (`active`/`archived`), `uploaded_by`, `created_at`, `updated_at` | 소프트 보관(숨김)과 물리 삭제 모두 지원 |

## 3. 향후 활용별 JOIN 설계

### 3.1 「버전 · 자료 현황」(`/?page=versions`) 버전별 스크린샷 건수·썸네일 집계
```sql
SELECT v.workspace_key AS version_id,
       a.artifact_kind,
       count(*) AS artifact_count,
       max(a.created_at) AS latest_at
FROM <schema>.sanity_oa_data v
LEFT JOIN <schema>.di_design_artifact a
  ON a.version_id = v.workspace_key
 AND a.project_id = v.project
 AND a.state = 'active'
WHERE v.record_type = 'WS_VERSION'
  AND v.project = $1
GROUP BY v.workspace_key, a.artifact_kind;
```
- 인덱스: `di_design_artifact_version_idx` 사용. `artifact_bytes`를 읽지 않아 빠름.

### 3.2 블록별 Floorplan 변천 타임라인 (`NET_VER → REVISION → ECO_NUM` 순 비교)
```sql
SELECT id, net_ver, revision, eco_num, layout, design_stage, view_name,
       width_px, height_px, title, caption, created_at
FROM <schema>.di_design_artifact
WHERE project_id = $1
  AND block_id = $2
  AND artifact_kind = 'floorplan'
  AND state = 'active'
ORDER BY net_ver DESC, revision DESC, eco_num DESC, created_at DESC;
```
- 인덱스: `di_design_artifact_business_key_idx` 사용. 좌우 나란히 비교 또는 슬라이더 오버레이 UI에 바로 연결.

### 3.3 프로젝트 비교(Area/Power) 화면에서 기준 vs 현재 Floorplan / Power Map 동시 표시
```sql
SELECT a.id, a.area_version_id, a.area_upload_id, a.artifact_kind,
       a.view_name, a.process_label, a.voltage_v, a.temp_c, a.pex, a.title
FROM <schema>.di_design_artifact a
WHERE a.area_version_id IN ($baseline_area_version_id, $current_area_version_id)
  AND a.artifact_kind IN ('floorplan', 'placement', 'power_map', 'ir_drop')
  AND a.state = 'active'
ORDER BY a.artifact_kind, a.created_at DESC;
```
- 특정 PTPX 코너 업로드(`di_area_power_csv.id`)에 묶인 Power Map/IR-drop은 `a.area_upload_id = u.id`로 직접 조인(`di_design_artifact_area_upload_idx`).

### 3.4 Sanity 보고서(DRC / Congestion / Lint 등)에 관련 스크린샷 첨부
```sql
SELECT a.id, a.artifact_kind, a.title, a.caption, a.mime_type, a.created_at
FROM <schema>.di_design_artifact a
WHERE a.sanity_report_id = $ws_report_key
  AND a.state = 'active'
ORDER BY a.created_at DESC;
```
- 인덱스: `di_design_artifact_sanity_report_idx` 사용.

### 3.5 중복 적재 자동 차단 및 향후 AI/VLM 연동
- `di_design_artifact_dedupe_uq`가 같은 `(project_id, block_id, version_id, artifact_kind, view_name, sha256)` 재업로드를 DB 레벨에서 차단합니다.
- 추후 이미지 기반 AI(VLM) 분석 도입 시 `metadata` JSONB에 매크로 이동·밀집 구역 요약/좌표를 저장하고, Area/Power 트리아지·확정 근거와 함께 참조할 수 있습니다.

## 4. 성능·운영 가이드
- **목록 API 금지 규칙**: 목록/현황 쿼리에서 `SELECT *` 또는 `artifact_bytes` 조회 금지. `id, project_id, ..., sha256, byte_length` 메타 컬럼만 읽고 바이너리는 `/artifacts/{id}/image`(원본) 또는 `/artifacts/{id}/thumbnail`(썸네일) 단건 엔드포인트로 분리.
- **권장 포맷**: 툴 출력 PNG를 그대로 저장하거나 업로드 시 WebP 무손실/고화질 변환 + 320px 썸네일 생성을 함께 저장하면 용량을 60~80% 절감할 수 있습니다.
- **정리 연동**: 프로젝트·블록·버전 삭제 시 `project_id` / `block_id` / `version_id` 조건으로 함께 삭제(또는 `state='archived'`)하도록 연결합니다.
