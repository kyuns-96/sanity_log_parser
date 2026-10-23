export const libraries = {
  studio: {
    name: "shadcn/studio",
    version: "",
    packageName: "shadcn-studio",
    description:
      "밑줄형 탭 · 검색 입력 · 선택형 라디오 · 체크박스 표 · 이력 시트",
    url: "https://shadcnstudio.com/components",
  },
};

const specs = [
  {
    id: "generated",
    name: "생성 클럭 정의 확인",
    count: 48,
    clock: "clk_cpu",
    mode: "FUNCTIONAL",
    classification:
      "같은 참조 클럭과 실행 모드를 가진 대상을 함께 묶었습니다. 다른 모드에서 관찰된 대상은 분리했습니다.",
    reason:
      "추출 로그에서 참조 클럭은 확인되지만 원본 설정과 실제 적용 조건을 대조할 근거가 부족합니다. 현재 자료만으로 PASS 또는 FAIL을 제안하기 어렵습니다.",
    draft:
      "원본 설정과 실행 조건을 추가로 확인한 뒤 판정합니다. 현재 로그만으로는 대상 관계의 유효성을 확정하기 어렵습니다.",
  },
  {
    id: "divider",
    name: "분주 관계 확인",
    count: 29,
    clock: "clk_div",
    mode: "FUNCTIONAL",
    classification:
      "동일한 분주 관계를 확인할 대상을 묶었습니다. 분주 값과 모드가 다른 대상은 다른 그룹으로 분리했습니다.",
    reason:
      "참조 관계가 추출되어 있으나 적용된 설정과 확인된 점검 기준이 제공되지 않았습니다. 설정 근거를 확보한 뒤 판정해야 합니다.",
    draft:
      "분주 설정과 해당 실행 모드의 원본 근거를 확인할 때까지 판정을 보류합니다.",
  },
  {
    id: "boundary",
    name: "도메인 경계 확인",
    count: 21,
    clock: "clk_aux",
    mode: "FUNCTIONAL",
    classification:
      "같은 두 도메인 사이의 경계를 함께 검토하도록 묶었습니다. 같은 경계라도 모드가 다르면 별도로 분리했습니다.",
    reason:
      "현재 추출 자료에는 경계의 적용 조건을 확인할 근거가 없습니다. 경계에 대한 원본 설정과 점검 기준을 추가로 확인해야 합니다.",
    draft:
      "도메인 경계와 적용 조건을 원본 설정에서 확인한 후 최종 판정을 내립니다.",
  },
  {
    id: "unused",
    name: "미사용 대상 확인",
    count: 17,
    clock: "clk_test",
    mode: "SCAN",
    classification:
      "같은 SCAN 모드에서 미사용 여부를 확인할 대상을 묶었습니다. 실제 제외 가능 여부는 판정 단계에서 검토합니다.",
    reason:
      "미사용이라는 추출 표식만으로 제외 가능 여부를 확인할 수 없습니다. 해당 모드의 유효한 제외 기준이 필요합니다.",
    draft:
      "SCAN 모드의 사용 여부와 확인된 제외 기준을 확보할 때까지 판정을 보류합니다.",
  },
  {
    id: "missing",
    name: "추가 근거 확인",
    count: 11,
    clock: "미확인",
    mode: "UNKNOWN",
    classification:
      "추출된 필드만으로 조건을 식별하기 어려운 대상을 분리했습니다. 원문 보충 후 그룹 구성을 다시 검토해야 합니다.",
    reason:
      "참조 클럭과 실행 조건이 확인되지 않았습니다. 필수 근거가 부족하므로 원문을 보충한 뒤 분류와 판정을 재검토해야 합니다.",
    draft: "원문과 누락된 실행 조건을 확보한 뒤 분류 및 판정을 재검토합니다.",
  },
];

let offset = 0;
export const groups = specs.map((group, index) => {
  const records = Array.from({ length: group.count }, (_, recordIndex) => {
    const number = offset + recordIndex + 1;
    return {
      id: "SYN-" + String(number).padStart(4, "0"),
      line: 120 + (number - 1) * 7,
      target:
        "u_cpu/" +
        ["u_clk_gen", "u_div", "u_boundary", "u_test", "u_unknown"][index] +
        "_" +
        String(recordIndex + 1).padStart(2, "0") +
        "/CK",
      clock: group.clock,
      mode: group.mode,
      groupId: group.id,
    };
  });
  offset += group.count;
  return { ...group, ordinal: index + 1, records };
});

export const totalRecords = groups.reduce((sum, group) => sum + group.count, 0);
export const pageSize = 6;

export function recordLines(record) {
  return [
    "# Synthetic evidence · UI comparison",
    "item        : CLK_0003",
    "target      : " + record.target,
    "reference   : " + record.clock,
    "mode        : " + record.mode,
    "evidence    : original configuration required",
    "record_id   : " + record.id,
  ];
}
