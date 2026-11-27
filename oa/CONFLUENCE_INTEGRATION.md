# 사내 Confluence Create-Only 연동 계약

사내 프로젝트 보안 권한 정책상 Confluence 토큰에는 **신규 생성(`POST`) 권한만 있고 조회(`GET`) · 수정(`PUT`) · 삭제(`DELETE`) 권한이 없습니다.** 공통 코드는 이 제약에 맞춰 단 한 번의 `GET`/`PUT`/`DELETE` 호출도 요구하지 않도록 설계되어 있습니다.

## 1. 명시적 연결 지점 (OA 담당자)

- 계약 모델: `common/floorplan/confluence.py` (`ConfluencePublisher`, `ConfluenceCreateRequest`, `ConfluenceAttachment`, `ConfluenceReceipt`, `ConfluenceNotConfigured`, `ConfluencePublishError`)
- 사내 구현 위치: `oa/integration/corporate_confluence.py:create_confluence_publisher()` 및 `oa/integration/company_confluence/`
- 공통 코드·SQL·SSO·UI·기존 테스트는 수정하지 않으며 새 테스트/fixture/mock/검증 스크립트 파일을 추가하지 않습니다.

## 2. Create-Only (`POST`-Only) 호출 순서

1. **개인 PAT 주입**: 각 사용자가 OA 화면에서 등록한 본인 Confluence PAT가 요청 시점에만 복호화되어 `req.personal_pat`로 전달됩니다(서버 로그나 예외 메시지에 토큰 원문을 절대 남기지 않음).
2. **신규 하위 페이지 생성 (`POST /rest/api/content`)**:
   - `req.space_key`와 숫자 `req.parent_page_id`(`ancestors: [{"id": req.parent_page_id}]`), 충돌 없는 고유 제목 `req.title`(`... (Rev.N · YYYY-MM-DD HH:MM)`), 그리고 첨부 예정 파일명(`<ri:attachment ri:filename="..."/>`)이 미리 포함된 `req.storage_xhtml`을 한 번에 `POST`합니다.
   - 응답 JSON에서 생성된 신규 `id`(`page_id`)와 링크를 추출합니다(`GET` 재조회 금지).
3. **경량 8-bit PNG 첨부 업로드 (`POST /rest/api/content/{page_id}/child/attachment`)**:
   - `req.attachments`의 각 항목을 `X-Atlassian-Token: no-check` 헤더와 함께 `POST` 업로드합니다. 본문 XHTML이 이미 동일 파일명을 참조하므로 페이지 본문 `PUT`(수정) 호출 없이 이미지가 표시됩니다.
4. **`ConfluenceReceipt(page_id=..., page_url=..., uploaded_attachments=...)` 반환**.
