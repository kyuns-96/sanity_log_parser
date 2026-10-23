import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ArrowSquareOut,
  CaretDown,
  CaretRight,
  Check,
  CheckCircle,
  Clock,
  Code,
  Copy,
  DownloadSimple,
  FileText,
  FolderSimple,
  GitBranch,
  Info,
  ListBullets,
  Moon,
  ShieldCheck,
  SlidersHorizontal,
  SquaresFour,
  Sun,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import * as UI from "./studio-kit";
import { groups, libraries, pageSize, recordLines, totalRecords } from "./data";

function verdictLabel(value) {
  if (value === "DEFER") return "보류";
  return value;
}
function verdictColor(value) {
  if (value === "PASS") return "success";
  if (value === "FAIL") return "danger";
  return "warning";
}
const initialKit = "studio";

export default function App() {
  const kit = initialKit;
  const [dark, setDark] = useState(
    new URLSearchParams(location.search).get("theme") === "dark",
  );
  const [activeId, setActiveId] = useState(groups[0].id);
  const [groupQuery, setGroupQuery] = useState("");
  const [groupFilter, setGroupFilter] = useState("all");
  const [recordQuery, setRecordQuery] = useState("");
  const [page, setPage] = useState(1);
  const [recordId, setRecordId] = useState(groups[0].records[0].id);
  const [tab, setTab] = useState("logs");
  const [checked, setChecked] = useState(new Set());
  const [drafts, setDrafts] = useState(
    Object.fromEntries(
      groups.map((group) => [
        group.id,
        { conclusion: "DEFER", reason: group.draft },
      ]),
    ),
  );
  const [confirmed, setConfirmed] = useState({});
  const [drawer, setDrawer] = useState(null);
  const drawerReturnRef = useRef(null);
  const [notice, setNotice] = useState("");
  const group = groups.find((value) => value.id === activeId);
  const library = libraries[kit];
  const draft = drafts[activeId];
  const final = confirmed[activeId];
  const dirty =
    final &&
    (final.conclusion !== draft.conclusion || final.reason !== draft.reason);
  const confirmedCount = Object.keys(confirmed).length;
  const deferredCount = Object.values(confirmed).filter(
    (value) => value.conclusion === "DEFER",
  ).length;
  const filteredGroups = groups.filter(
    (value) =>
      value.name.includes(groupQuery.trim()) &&
      (groupFilter === "all" ||
        (groupFilter === "confirmed"
          ? confirmed[value.id]
          : !confirmed[value.id])),
  );
  const filteredRecords = useMemo(() => {
    const query = recordQuery.toLowerCase().trim();
    return group.records.filter((record) =>
      [record.id, record.target, record.mode, String(record.line)].some(
        (value) => value.toLowerCase().includes(query),
      ),
    );
  }, [group, recordQuery]);
  const pages = Math.max(1, Math.ceil(filteredRecords.length / pageSize));
  const rows = filteredRecords.slice((page - 1) * pageSize, page * pageSize);
  const record =
    group.records.find((value) => value.id === recordId) || group.records[0];

  useEffect(() => {
    document.documentElement.dataset.mode = dark ? "dark" : "light";
    document.documentElement.dataset.library = kit;
    document.documentElement.classList.toggle("dark", dark);
    document.title = "Sanity · " + library.name + " 시안";
    const url = new URL(location.href);
    url.hash = kit;
    if (dark) url.searchParams.set("theme", "dark");
    else url.searchParams.delete("theme");
    history.replaceState(null, "", url);
  }, [kit, dark, library.name]);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 4400);
    return () => clearTimeout(timer);
  }, [notice]);

  function selectGroup(id) {
    const next = groups.find((value) => value.id === id);
    setActiveId(id);
    setPage(1);
    setRecordId(next.records[0].id);
    setRecordQuery("");
    setChecked(new Set());
    setTab("logs");
  }
  function searchRecords(value) {
    setRecordQuery(value);
    setPage(1);
    const query = value.toLowerCase().trim();
    const next = group.records.find((row) =>
      [row.id, row.target, row.mode, String(row.line)].some((field) =>
        field.toLowerCase().includes(query),
      ),
    );
    if (next) setRecordId(next.id);
  }
  function changePage(next) {
    const bounded = Math.min(Math.max(next, 1), pages);
    setPage(bounded);
    if (filteredRecords[(bounded - 1) * pageSize])
      setRecordId(filteredRecords[(bounded - 1) * pageSize].id);
  }
  function checkRecord(id, value) {
    setChecked((previous) => {
      const next = new Set(previous);
      if (value) next.add(id);
      else next.delete(id);
      return next;
    });
  }
  function updateDraft(field, value) {
    setDrafts((previous) => ({
      ...previous,
      [activeId]: { ...previous[activeId], [field]: value },
    }));
  }
  function confirmGroup() {
    if (draft.reason.trim().length < 10) return;
    setConfirmed((previous) => ({
      ...previous,
      [activeId]: {
        ...draft,
        savedAt: new Date().toLocaleTimeString("ko-KR", {
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        }),
      },
    }));
    setNotice(
      group.name +
        " · " +
        verdictLabel(draft.conclusion) +
        " 판정을 시안 메모리에 저장했습니다.",
    );
  }
  function download() {
    const content = group.records
      .map((value) => recordLines(value).join("\n"))
      .join("\n\n");
    const url = URL.createObjectURL(
      new Blob([content], { type: "text/plain;charset=utf-8" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "synthetic-" + group.id + ".txt";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(recordLines(record).join("\n"));
      setNotice("선택한 합성 원문을 복사했습니다.");
    } catch {
      setNotice(
        "브라우저에서 복사를 허용하지 않았습니다. 원문을 선택해 복사할 수 있습니다.",
      );
    }
  }

  const filterOptions = [
    { value: "all", label: "모든 그룹 · 5" },
    { value: "pending", label: "미확정 · " + (5 - confirmedCount) },
    { value: "confirmed", label: "확정 · " + confirmedCount },
  ];

  return (
    <UI.KitProvider>
      <div className="comparison" data- data-mode={dark ? "dark" : "light"}>
        <a href="#workbench" className="skip-link">
          검토 화면으로 건너뛰기
        </a>
        <div className="comparison-bar">
          <div className="comparison-caption">
            <SlidersHorizontal size={17} />
            <span>shadcn/studio 시안</span>
          </div>
          <nav className="library-switch" aria-label="적용한 컴포넌트">
            <a
              className="active"
              href={library.url}
              target="_blank"
              rel="noreferrer"
            >
              <span className="library-dot studio" />
              {library.name}
            </a>
          </nav>
          <span className="comparison-helper">
            공개 컴포넌트 적용 · 합성 로그
          </span>
          <button
            className="mode-switch"
            type="button"
            aria-label={dark ? "밝은 화면으로 전환" : "어두운 화면으로 전환"}
            onClick={() => setDark((value) => !value)}
          >
            {dark ? <Sun size={17} /> : <Moon size={17} />}
            <span>{dark ? "Light" : "Dark"}</span>
          </button>
        </div>

        <header className="product-header">
          <div className="brand">
            <span className="brand-symbol">
              <SquaresFour size={19} weight="fill" />
            </span>
            <strong>sanity</strong>
            <span className="brand-divider" />
            <span className="product-name">검토 워크벤치</span>
          </div>
          <div className="scope-breadcrumb">
            <span>Atlas</span>
            <CaretRight size={12} />
            <span>CPU</span>
            <CaretRight size={12} />
            <span>NET_002</span>
            <CaretRight size={12} />
            <span className="scope-stage">POST · chip_finish</span>
          </div>
          <div className="product-user">
            <span className="demo-label">합성 시안</span>
            <span className="avatar">검</span>
            <span>검토자</span>
          </div>
        </header>

        <main className="workbench-main" id="workbench">
          <section className="report-heading" aria-label="현재 검토 보고서">
            <div className="report-title">
              <div className="report-icon">
                <FileText size={22} />
              </div>
              <div>
                <div className="title-line">
                  <h1>CLK_0003</h1>
                  <span className="report-caption">그룹별 판정</span>
                  <UI.Tag color="neutral">GCA</UI.Tag>
                </div>
                <p>원문을 확인하고, 각 그룹의 판정과 근거를 확정하세요.</p>
              </div>
            </div>
            <div className="report-actions">
              <UI.Button
                quiet
                icon={<Clock size={16} />}
                onClick={(event) => {
                  drawerReturnRef.current = event.currentTarget;
                  setDrawer("history");
                }}
              >
                검토 이력
              </UI.Button>
              <UI.Button icon={<DownloadSimple size={16} />} onClick={download}>
                합성 원문
              </UI.Button>
            </div>
          </section>

          <section className="progress-toolbar" aria-label="검토 진행">
            <div className="stage-track">
              <span className="stage-complete">
                <CheckCircle size={17} weight="fill" />
                로그 분류 확정
              </span>
              <CaretRight size={12} />
              <span className="stage-active">
                <span className="stage-number">2</span>그룹별 판정
              </span>
            </div>
            <div className="review-progress">
              <span className="progress-label">
                사람 확정 <strong>{confirmedCount}</strong>
                <span> / 5</span>
              </span>
              <div
                className="mini-progress"
                aria-label={"5개 그룹 중 " + confirmedCount + "개 확정"}
              >
                <span style={{ width: (confirmedCount / 5) * 100 + "%" }} />
              </div>
            </div>
            <div className="original-result">
              <span>도구 결과</span>
              <UI.Tag color="danger">FAIL</UI.Tag>
              <span className="toolbar-divider" />
              <span>
                전체 로그 <strong>{totalRecords}</strong>
              </span>
            </div>
          </section>

          <section
            className="workbench-grid"
            aria-label="그룹과 원문, 최종 판정"
          >
            <aside className="project-pane" aria-label="보고서 맥락">
              <div className="pane-heading">
                <span>프로젝트</span>
                <UI.IconButton
                  label="보고서 범위 정보"
                  onClick={(event) => {
                    drawerReturnRef.current = event.currentTarget;
                    setDrawer("scope");
                  }}
                >
                  <Info size={17} />
                </UI.IconButton>
              </div>
              <div className="project-tree">
                <div className="tree-project">
                  <CaretDown size={12} />
                  <FolderSimple size={16} />
                  <strong>Atlas</strong>
                </div>
                <div className="tree-block">
                  <CaretDown size={12} />
                  <span>CPU</span>
                </div>
                <div className="tree-version">
                  NET_002 <small>POST</small>
                </div>
                <div className="tree-eco">
                  <span className="tree-line" />
                  chip_finish
                </div>
                <div className="tree-report">
                  <FileText size={15} />
                  <span>CLK_0003</span>
                  <span className="active-dot" />
                </div>
              </div>
              <div className="context-details">
                <div className="small-section-title">보고서 맥락</div>
                <dl>
                  <div>
                    <dt>현재 분류</dt>
                    <dd>r4 · 확정</dd>
                  </div>
                  <div>
                    <dt>검토 대상</dt>
                    <dd>{totalRecords}건</dd>
                  </div>
                  <div>
                    <dt>검토 단위</dt>
                    <dd>5개 그룹</dd>
                  </div>
                </dl>
              </div>
              <div className="project-bottom">
                <ShieldCheck size={18} />
                <p>
                  원본 도구 결과와
                  <br />
                  사람의 최종 판정을
                  <br />
                  각각 보존합니다.
                </p>
              </div>
            </aside>

            <aside className="group-pane" aria-label="분류 그룹 목록">
              <div className="pane-heading">
                <span>분류 그룹</span>
                <span className="heading-count">5</span>
              </div>
              <div className="group-controls">
                <UI.Search
                  label="그룹 검색"
                  placeholder="그룹 검색"
                  value={groupQuery}
                  onChange={setGroupQuery}
                />
                <UI.Select
                  label="그룹 확정 상태"
                  options={filterOptions}
                  value={groupFilter}
                  onChange={setGroupFilter}
                />
              </div>
              <div className="group-list">
                {filteredGroups.map((value) => {
                  const saved = confirmed[value.id];
                  return (
                    <button
                      key={value.id}
                      className={
                        "group-item " +
                        (value.id === activeId ? "selected" : "")
                      }
                      aria-pressed={value.id === activeId}
                      onClick={() => selectGroup(value.id)}
                      data-testid={"group-" + value.id}
                    >
                      <span className="group-ordinal" aria-hidden="true">
                        {String(value.ordinal).padStart(2, "0")}
                      </span>
                      <span className="group-info">
                        <strong>{value.name}</strong>
                        <span>
                          {saved ? (
                            <>
                              <Check size={12} />
                              {verdictLabel(saved.conclusion)} · 확정
                            </>
                          ) : (
                            <>
                              <span className="pending-dot" />
                              최종 판정 전
                            </>
                          )}
                        </span>
                      </span>
                      <span className="group-count">{value.count}</span>
                    </button>
                  );
                })}
                {!filteredGroups.length && (
                  <div className="empty-list">
                    <ListBullets size={22} />
                    <p>조건에 맞는 그룹이 없습니다.</p>
                    <button
                      onClick={() => {
                        setGroupQuery("");
                        setGroupFilter("all");
                      }}
                    >
                      필터 초기화
                    </button>
                  </div>
                )}
              </div>
              <div className="group-pane-footer">
                <span>현재 분류 r4</span>
                <UI.Tag color="neutral">사람 확정</UI.Tag>
              </div>
            </aside>

            <section
              className="evidence-pane"
              aria-label="선택 그룹의 로그와 원문"
            >
              <div className="evidence-heading">
                <div>
                  <h2>{group.name}</h2>
                  <span>
                    {group.count}개 로그 <span className="meta-dot">·</span>{" "}
                    {group.clock}
                  </span>
                </div>
                <UI.Tooltip label="선택 그룹의 합성 원문 내려받기">
                  <span>
                    <UI.IconButton
                      label="현재 그룹 합성 원문 내려받기"
                      onClick={download}
                    >
                      <DownloadSimple size={18} />
                    </UI.IconButton>
                  </span>
                </UI.Tooltip>
              </div>
              <UI.Tabs
                value={tab}
                onChange={setTab}
                label="로그와 근거 보기"
                items={[
                  {
                    value: "logs",
                    label: "로그 목록",
                    icon: <ListBullets size={16} />,
                  },
                  {
                    value: "basis",
                    label: "분류 근거",
                    icon: <GitBranch size={16} />,
                  },
                  {
                    value: "history",
                    label: "이력",
                    icon: <Clock size={16} />,
                  },
                ]}
              />
              {tab === "logs" ? (
                <div
                  className="evidence-tab-panel"
                  role="tabpanel"
                  id="pane-logs"
                  aria-labelledby="tab-logs"
                >
                  <div className="record-toolbar">
                    <UI.Search
                      label="로그 검색"
                      placeholder="대상 경로·ID 검색"
                      value={recordQuery}
                      onChange={searchRecords}
                    />
                    <span className="record-count">
                      {checked.size
                        ? checked.size + "건 선택"
                        : filteredRecords.length + "건"}
                      {checked.size > 0 && (
                        <UI.IconButton
                          label="로그 선택 해제"
                          onClick={() => setChecked(new Set())}
                        >
                          <X size={13} />
                        </UI.IconButton>
                      )}
                    </span>
                  </div>
                  <div className="record-table">
                    {rows.length ? (
                      <UI.LogTable
                        rows={rows}
                        selectedId={recordId}
                        onSelect={setRecordId}
                        checked={checked}
                        onCheck={checkRecord}
                      />
                    ) : (
                      <div className="empty-records">검색 결과가 없습니다.</div>
                    )}
                  </div>
                  <div className="record-pagination">
                    <span>
                      {filteredRecords.length ? (page - 1) * pageSize + 1 : 0}–
                      {Math.min(page * pageSize, filteredRecords.length)}{" "}
                      <span className="subtle">
                        / {filteredRecords.length}건
                      </span>
                    </span>
                    <div>
                      <UI.IconButton
                        label="이전 로그 페이지"
                        disabled={page <= 1}
                        onClick={() => changePage(page - 1)}
                      >
                        <CaretRight size={15} className="flip" />
                      </UI.IconButton>
                      <span>
                        {page}
                        <span className="subtle"> / {pages}</span>
                      </span>
                      <UI.IconButton
                        label="다음 로그 페이지"
                        disabled={page >= pages}
                        onClick={() => changePage(page + 1)}
                      >
                        <CaretRight size={15} />
                      </UI.IconButton>
                    </div>
                  </div>
                  <section
                    className="source-pane"
                    aria-label="선택한 로그 원문"
                  >
                    <div className="source-header">
                      <div>
                        <Code size={16} />
                        <strong>원문 근거</strong>
                        <span className="source-file">
                          synthetic-report.rpt
                        </span>
                      </div>
                      <UI.Tooltip label="선택한 원문 복사">
                        <span>
                          <UI.IconButton label="원문 복사" onClick={copy}>
                            <Copy size={16} />
                          </UI.IconButton>
                        </span>
                      </UI.Tooltip>
                    </div>
                    <div className="source-content">
                      <div className="source-id">
                        {record.id}
                        <span>
                          L{record.line}–{record.line + 6}
                        </span>
                      </div>
                      <pre>
                        {recordLines(record).map((line, index) => (
                          <div
                            key={index}
                            className={index === 5 ? "source-highlight" : ""}
                          >
                            <span className="source-line-number">
                              {record.line + index}
                            </span>
                            <code>{line}</code>
                          </div>
                        ))}
                      </pre>
                    </div>
                  </section>
                </div>
              ) : tab === "basis" ? (
                <div
                  className="detail-content"
                  role="tabpanel"
                  id="pane-basis"
                  aria-labelledby="tab-basis"
                >
                  <div className="detail-eyebrow">
                    <CheckCircle size={17} />
                    사람이 확정한 분류 · r4
                  </div>
                  <h3>왜 함께 묶었는가</h3>
                  <p>{group.classification}</p>
                  <h3>이번 그룹의 공통 조건</h3>
                  <dl className="basis-fields">
                    <div>
                      <dt>참조 클럭</dt>
                      <dd>{group.clock}</dd>
                    </div>
                    <div>
                      <dt>실행 모드</dt>
                      <dd>{group.mode}</dd>
                    </div>
                    <div>
                      <dt>구성원</dt>
                      <dd>{group.count}건 · 전체 구성 확정</dd>
                    </div>
                  </dl>
                  <p className="detail-note">
                    화면 비교를 위한 합성 분류 근거입니다.
                  </p>
                </div>
              ) : (
                <div
                  className="detail-content"
                  role="tabpanel"
                  id="pane-history"
                  aria-labelledby="tab-history"
                >
                  <HistoryContent final={final} />
                </div>
              )}
            </section>

            <aside className="decision-pane" aria-label="판정과 근거">
              <div className="pane-heading">
                <span>판정과 근거</span>
                {final ? (
                  <UI.Tag color={dirty ? "warning" : "success"}>
                    {dirty ? "수정 중" : "사람 확정"}
                  </UI.Tag>
                ) : (
                  <span className="pending-label">미확정</span>
                )}
              </div>
              <div className="decision-content">
                <details className="classification-summary">
                  <summary>
                    <GitBranch size={16} />
                    <span>확정한 분류 근거</span>
                    <span className="revision-label">r4</span>
                    <CaretDown size={14} />
                  </summary>
                  <p>{group.classification}</p>
                </details>
                <section
                  className="ai-recommendation"
                  aria-label="AI 판정 제안"
                >
                  <div className="section-title">
                    <span className="ai-mark">AI</span>
                    <h3>판정 제안</h3>
                    <span>합성 예시</span>
                  </div>
                  <div className="ai-verdict">
                    <UI.Tag color="warning">보류</UI.Tag>
                    <span>추가 원본 근거 필요</span>
                  </div>
                  <p>{group.reason}</p>
                  <div className="evidence-links">
                    <button
                      onClick={() => {
                        setTab("logs");
                        setRecordId(group.records[0].id);
                        setRecordQuery("");
                        setPage(1);
                      }}
                    >
                      원문 근거 {group.count}건<ArrowSquareOut size={12} />
                    </button>
                    <span>참고 사례 0건</span>
                  </div>
                </section>
                <section
                  className="human-decision"
                  aria-label="사람의 최종 판정 입력"
                >
                  <div className="section-title">
                    <span className="human-divider" />
                    <h3>사람의 최종 판정</h3>
                  </div>
                  <UI.Verdict
                    value={draft.conclusion}
                    onChange={(value) => updateDraft("conclusion", value)}
                    label="사람의 최종 판정"
                  />
                  <div className="reason-heading">
                    <span id="reason-label">판정 근거</span>
                    <span>필수</span>
                  </div>
                  <UI.Textarea
                    value={draft.reason}
                    onChange={(value) => updateDraft("reason", value)}
                    label="판정 근거"
                  />
                  <p
                    className={
                      "input-hint " +
                      (draft.reason.trim().length < 10 ? "invalid" : "")
                    }
                  >
                    {draft.reason.trim().length < 10
                      ? "확정할 근거를 10자 이상 입력하세요."
                      : "AI 제안과 다른 판단이라면 정정 이유를 함께 남깁니다."}
                  </p>
                </section>
                <div className="record-distinction">
                  <Info size={14} />
                  <span>도구 결과, AI 제안, 최종 판정을 각각 기록합니다.</span>
                </div>
              </div>
              <div className="decision-actions">
                <div className="save-context">
                  {final ? (
                    <>
                      <CheckCircle size={14} />
                      시안 메모리 저장 · {final.savedAt}
                    </>
                  ) : (
                    <>
                      <span className="pending-dot" />
                      아직 확정하지 않았습니다.
                    </>
                  )}
                </div>
                <UI.Button
                  primary
                  onClick={confirmGroup}
                  disabled={draft.reason.trim().length < 10}
                  data-testid="confirm-verdict"
                >
                  <span>{final ? "판정 수정 확정" : "이 그룹 판정 확정"}</span>
                  <ArrowRight size={17} />
                </UI.Button>
              </div>
            </aside>
          </section>

          <footer className="workspace-footer">
            <span>
              <span className="live-dot" />
              합성 데이터 · 실제 AI·DB 연결 없음 · 새로고침 시 초기화
            </span>
            <span className="completion-note">
              보류 {deferredCount} <span>·</span> 미확정 {5 - confirmedCount}{" "}
              <span>·</span>{" "}
              {deferredCount || confirmedCount < 5
                ? "검토 진행 중"
                : "모든 그룹 판정 완료"}
            </span>
            <a href={library.url} target="_blank" rel="noreferrer">
              {library.name} {library.version}
              <ArrowSquareOut size={12} />
            </a>
          </footer>
        </main>

        <div className="library-note">
          <strong>{library.name}</strong>
          <span>{library.description}</span>
          <span className="installed-label">공식 소스 적용</span>
        </div>
        {notice && (
          <div className="notice" role="status">
            <CheckCircle size={18} />
            <span>{notice}</span>
            <button
              type="button"
              aria-label="알림 닫기"
              onClick={() => setNotice("")}
            >
              <X size={16} />
            </button>
          </div>
        )}
        <UI.Drawer
          returnFocusRef={drawerReturnRef}
          opened={Boolean(drawer)}
          onClose={() => setDrawer(null)}
          title={
            drawer === "history" ? "현재 그룹의 검토 이력" : "보고서 범위 정보"
          }
        >
          {drawer === "history" ? (
            <HistoryContent final={final} />
          ) : (
            <div className="drawer-scope">
              <p>실제 라이브러리 컨트롤을 비교하기 위한 합성 워크벤치입니다.</p>
              <dl className="basis-fields">
                <div>
                  <dt>프로젝트</dt>
                  <dd>Atlas / CPU</dd>
                </div>
                <div>
                  <dt>버전</dt>
                  <dd>NET_002 / POST</dd>
                </div>
                <div>
                  <dt>원천 실행</dt>
                  <dd>chip_finish</dd>
                </div>
                <div>
                  <dt>로그</dt>
                  <dd>합성 {totalRecords}건</dd>
                </div>
                <div>
                  <dt>현재 분류</dt>
                  <dd>r4 · 5개 그룹</dd>
                </div>
              </dl>
              <p>
                선택·검색·판정 변경은 현재 탭 메모리에서만 동작합니다. 회사
                보고서·SSO·DB·모델 API는 연결하지 않았습니다.
              </p>
            </div>
          )}
        </UI.Drawer>
      </div>
    </UI.KitProvider>
  );
}

function HistoryContent({ final }) {
  return (
    <div className="history-content">
      <p className="history-intro">현재 시안의 합성 이력입니다.</p>
      {final && (
        <div className="history-event">
          <span className="history-dot saved" />
          <div>
            <strong>
              사람의 판정 확정 <span>{final.savedAt}</span>
            </strong>
            <p>
              {verdictLabel(final.conclusion)} · {final.reason}
            </p>
            <small>현재 탭 메모리에 저장</small>
          </div>
        </div>
      )}
      <div className="history-event">
        <span className="history-dot" />
        <div>
          <strong>
            그룹별 판정 제안 <span>09:46</span>
          </strong>
          <p>추가 근거를 요청하는 보류 제안</p>
          <small>실제 모델 호출 없는 합성 예시</small>
        </div>
      </div>
      <div className="history-event">
        <span className="history-dot" />
        <div>
          <strong>
            로그 분류 확정 <span>09:42</span>
          </strong>
          <p>126개 로그를 5개 그룹으로 분류 · r4</p>
          <small>그룹 구성과 그룹별 분류 근거 확정</small>
        </div>
      </div>
    </div>
  );
}
