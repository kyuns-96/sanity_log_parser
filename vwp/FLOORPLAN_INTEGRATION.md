# VWP Floorplan 수집 및 BIST 그룹 매핑 연동 계약

## 1. 핵심 보안·용량 원칙 (원본 DEF / LEF 업로드 절대 금지)

- **원본 `DEF(.def / .def.gz)` · `LEF(.lef / .lef.gz)` · `GDS/OAS` 파일은 VWP 로컬에서만 스트리밍 파싱하며, VWP-bridge나 PostgreSQL DB로 절대 전송·저장하지 않습니다.**
- `vwp_bridge`와 `oa` 서버는 확장자 및 본문 헤더(`DESIGN ... ;`, `UNITS DISTANCE MICRONS`, `MACRO ...` 등) 검사(`reject_raw_def_or_lef`)로 원본 DEF/LEF 유입을 차단합니다.
- DB(`di_design_artifact` + `di_area_power_tracking_record`)에는 오직 정규화된 경량 `image/svg+xml` 도면과 요약 `FloorplanScene` JSON(외곽선, 매크로/SRAM 박스·방향, BIST 그룹 요약, 모듈별 타일 밀도)만 적재됩니다.

## 2. 사내 BIST 매핑 어댑터 연결 지점 (VWP 담당자)

- 파일 위치: `vwp/integration/corporate_floorplan.py:extract_bist_groups()` 및 선택 보조 패키지 `vwp/integration/company_floorplan/`
- 입력: 추출된 SRAM 인스턴스 목록 `[{"inst_path": "u_l1/sram_0", "master": "SRAM_256x64", "bbox_um": [llx, lly, urx, ury]}]` 및 실행 디렉터리 `work_dir`
- 반환 형식:
  ```python
  {
      "u_l1/sram_0": {"bist_group": "MBIST_G0", "controller": "u_mbist_0"},
      "u_l1/sram_1": {"bist_group": "MBIST_G0", "controller": "u_mbist_0"},
  }
  ```
- 어댑터 연결 전에도 `--bist-map <bist.json|bist.csv>` 옵션으로 즉시 실행·검증할 수 있습니다.

## 3. CLI 실행 예시

```bash
python -m vwp.floorplan \
  --project ProjA --block GPU_SHADER \
  --fp-ver FP_v1.2 --prev-fp-ver FP_v1.1 \
  --scope-kind subblock \
  --net-ver NET_04 --revision rev2 --layout POST --eco-num ECO_01 \
  --def ./pnr/GPU_SHADER.def.gz \
  --lef ./lef/tech.lef ./lef/sram_macros.lef \
  --use-corporate-bist
```
