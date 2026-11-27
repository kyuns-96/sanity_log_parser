# VWP Floorplan 수집 · Hard Macro 분류 · 사내 Instance 속성 및 BIST 연동 계약

## 1. 핵심 보안·용량 원칙 (원본 DEF / LEF 업로드 절대 금지)

- **원본 `DEF(.def / .def.gz)` · `LEF(.lef / .lef.gz)` · `GDS/OAS` 파일은 VWP 로컬에서만 스트리밍 파싱하며, VWP-bridge나 PostgreSQL DB로 절대 전송·저장하지 않습니다.**
- `vwp_bridge`와 `oa` 서버는 확장자 및 본문 헤더(`DESIGN ... ;`, `UNITS DISTANCE MICRONS`, `MACRO ...` 등) 검사(`reject_raw_def_or_lef`)로 원본 DEF/LEF 유입을 차단합니다.
- DB(`di_design_artifact` 또는 임시 우회 `di_area_power_tracking_record`)에는 오직 정규화된 경량 `image/svg+xml` 도면과 요약 `FloorplanScene` JSON(외곽선, 매크로/SRAM 박스·방향·정제 속성, BIST 그룹 요약, 모듈별 타일 밀도)만 적재됩니다.

## 2. 사내 Hard Macro 분류 및 Instance 속성 어댑터 (`FloorplanMacroClassifier`)

내부에서만 알 수 있는 마스터 명명 규칙, 비표준 LEF `CLASS`, 정확한 Hard Macro 인스턴스 목록, 인스턴스별 사내 속성(`power_domain`, `voltage_domain`, `macro_family`, `owner_team`, BIST 정보 등)은 **VWP 담당 내부 AI가 추상 계약(`common/floorplan/classifier.py:FloorplanMacroClassifier`)을 구현**합니다.

- **허용 수정 위치**:
  - 팩토리 등록: `vwp/integration/corporate_floorplan.py:create_macro_classifier()`
  - 사내 구현 패키지: `vwp/integration/company_floorplan/`
- **추상 계약 (`common/floorplan/classifier.py` — 읽기 전용)**:
  1. `prepare(*, work_dir, project, block, fp_ver, scope_kind, lef_macros)`: `fork` 병렬 워커 분기 전에 **1회** 실행됩니다. 사내 로컬 리포트/속성 파일만 읽기 전용으로 로드합니다.
  2. `classify_master(master, lef_info, *, scope_kind) -> MacroClassification | None`: 고유 `master`당 **1회만 호출**(파서가 메모이즈)됩니다.
     - Hard Macro면 `MacroClassification(is_hard_macro=True, kind="sram"|"ip"|"subblock"|"macro", width_um=..., height_um=...)` 반환
     - 강제 표준셀 제외면 `MacroClassification(is_hard_macro=False)` 반환
     - 기본 LEF/마스터 판정에 위임하려면 `None` 반환
     - **주의**: 표준셀 수백만 개의 `inst_name` 전체 계층 경로에 정규식을 매칭하지 않습니다(예: `u_sram_ctrl/.../U1234` 오탐 방지).
  3. `exact_hard_macro_instances() -> set[str]`: 사내 리포트/DB가 정확한 Hard Macro 인스턴스 경로 집합을 이미 알고 있을 때 반환하면 파서가 `O(1)` 바이트 집합 조회로 해당 인스턴스만 정확히 승격합니다.
  4. `enrich_macro_instance(inst_name, master, base, *, status, llx, lly, orient) -> MacroClassification | None`: 판별된 Hard Macro 인스턴스에만 호출됩니다. 사내 인스턴스 속성(`attributes: dict`, 키 24개·값 200자 이내 스칼라만 자동 정제)과 `kind`/`bist_group`/`width_um`/`height_um`을 보강하거나, 제외할 인스턴스면 `None`을 반환합니다.
- `create_macro_classifier()`가 구현되어 `FloorplanMacroClassifier`를 반환하면 `python -m vwp.floorplan`이 자동 사용하며, `--use-corporate-classifier`를 주면 미연결 시 즉시 실패(`CorporateClassifierNotConfigured`)로 검증할 수 있습니다.

## 3. 사내 BIST 매핑 어댑터 연결 지점 (기존 호환 유지)

- 파일 위치: `vwp/integration/corporate_floorplan.py:extract_bist_groups()` 및 `vwp/integration/company_floorplan/`
- `FloorplanMacroClassifier.enrich_macro_instance`에서 `bist_group`/`bist_controller`를 직접 채우거나, 기존처럼 `extract_bist_groups` (`--use-corporate-bist`) 또는 `--bist-map <bist.json|bist.csv>`를 사용할 수 있습니다.
- 반환 형식:
  ```python
  {
      "u_l1/sram_0": {"bist_group": "MBIST_G0", "controller": "u_mbist_0"},
      "u_l1/sram_1": {"bist_group": "MBIST_G0", "controller": "u_mbist_0"},
  }
  ```

## 4. CLI 실행 예시

```bash
python -m vwp.floorplan \
  --project ProjA --block GPU_SHADER \
  --fp-ver FP_v1.2 --prev-fp-ver FP_v1.1 \
  --scope-kind subblock \
  --net-ver NET_04 --revision rev2 --layout POST --eco-num ECO_01 \
  --def ./pnr/GPU_SHADER.def \
  --lef ./lef/tech.lef ./lef/sram_macros.lef \
  --use-corporate-classifier --use-corporate-bist \
  --url https://vwp-bridge.internal:8081
```
