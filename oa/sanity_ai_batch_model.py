"""Complete leaf classification and bounded semantic reduction; no DB writes."""
from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_clustering_advice import evidence, partition, validate_clustering_advice
from common.sanity_ai_knowledge import shared_case
from common.sanity_preserved_ai import split_reply, model_metadata
from common.sanity_submission import check
from oa.sanity_ai_budget import TokenBudget, BATCH_PROMPT_VERSION
from oa.sanity_ai_model import _CLUSTER_PROMPT, source_instruction

VERSION = BATCH_PROMPT_VERSION
LEAF_PROMPT = _CLUSTER_PROMPT + """
이번 요청은 고정된 전체 report의 일부를 빠짐없이 소유하는 leaf 배치입니다.
모든 report.records를 검토하고 이번 배치의 짧은 record ID로 정확히 배정하세요.
batch.record_aliases가 원본 ID와의 대응입니다. 배치 밖 원본을 배정하지 마세요.
이번 결과는 중간 분류이며 전체 report 완료나 사람 확정이 아닙니다.
그룹별 이유는 공통 조건, 구분 기준, 반례/예외/미확인을 빠뜨리지 않고 간결하게 작성하세요.
"""
MERGE_PROMPT = _CLUSTER_PROMPT + """
이번 records는 원본 로그가 아니라 앞선 AI가 전체 구성원을 읽고 만든 중간 그룹입니다.
각 record의 fields에는 이전 분류 근거·조건·예외·대표 원본 참조가 있습니다.
이번 요청은 중간 group ID 전체를 유지하거나 의미상 같은 그룹으로 통합하는 작업입니다.
group ID를 정확히 한 번씩 배정하고, 이름/code/비슷한 문구만으로 병합하지 마세요.
각 원본 전체를 이번 호출에서 다시 읽었다고 주장하지 마세요. 이전 검토의 근거를 비교하세요.
합쳐질 그룹 전체의 조건·mode·구분 이유·반례·미확인을 확인하세요. A-B, B-C 유사성만으로 전이 병합하지 마세요.
의미가 다르거나 확인할 수 없으면 별도 그룹을 유지하세요. 입력 그룹 내부의 구성원을 재배정하지 마세요.
병합 근거에는 예외·충돌·조건 차이를 보존하세요. evidence_ids는 실제 참고한 이번 중간 group ID입니다.
reference_groups에는 이번 입력 cases에 제공된 과거 사람 확정 사례만 인용하세요.
"""


class BatchModel:
    def __init__(self, advisor, context, cases, fields):
        self.advisor, self.context, self.cases, self.fields = advisor, context, cases, fields
        self.settings = advisor.settings.batch
        self.budget = TokenBudget(self.settings)
        self.records = evidence(context)

    def model_context(self):
        return self.context

    def instruction(self, context, stage):
        return source_instruction(context, LEAF_PROMPT if stage == 'LEAF' else MERGE_PROMPT,
                                  VERSION+'.'+stage.lower())

    def citation_context(self, context):
        return context

    def check_citations(self, citations):
        pass

    def packet(self, rows, *, stage, details):
        context = self.advisor._context(self.model_context())
        report = {**context['report'], 'records':rows, 'record_count':len(rows)}
        # SOURCE_PART manifests remain audit metadata on the server; they are
        # not another copy of the original records for the model to classify.
        report.pop('source_parts',None)
        context = {**context,'report':report}
        packet = {'context':context,'cases':[shared_case(c) for c in self.cases],
                  'identity_fields':self.fields,'batch':{'version':getattr(self,'version',VERSION),'stage':stage,
                  'source_record_count':len(self.records),'source_sha256':self.context['source_sha256'],**details}}
        instruction, version = self.instruction(context, stage)
        return packet, instruction, version, context

    def leaf(self, start, stop):
        aliases = {'r'+str(i):r['id'] for i,r in enumerate(self.records[start:stop])}
        rows = [{**r,'id':'r'+str(i)} for i,r in enumerate(self.records[start:stop])]
        return self.packet(rows,stage='LEAF',details={'start':start,'stop':stop,'record_aliases':aliases})

    @staticmethod
    def summary(node):
        return {'id':node['id'],'raw':node['label'],'fields':{
            'reason':node['reason'],'record_count':len(node['members']),
            'evidence_indices':node['evidence'], 'reference_groups':node['references'],
            'conditions':node['conditions'], 'membership_sha256':digest(node['members'])}}

    def merge(self, nodes):
        return self.packet([self.summary(n) for n in nodes],stage='MERGE',details={'group_count':len(nodes)})

    def fits(self, prepared):
        packet, instruction = prepared[:2]
        # Bound the nested JSON string and gateway envelope before dispatch.
        # _call also checks the exact final body, including its actual session ID.
        wire_bound = 2*(len(canonical_json(packet))+len(instruction.encode('utf-8')))+4096
        return wire_bound <= self.advisor.settings.max_input_bytes and self.budget.measure(packet,instruction) <= self.settings.input_limit

    def leaf_plan(self):
        result, start = [], 0
        # Reserve output for a difficult batch with many distinct groups. This
        # is a planning estimate; incomplete/over-limit responses still fail.
        limit = min(self.settings.max_records,max(1,self.settings.output_tokens//192))
        while start < len(self.records):
            lo, hi, count = 1, min(limit,len(self.records)-start), 0
            while lo <= hi:
                mid = (lo+hi)//2
                if self.fits(self.leaf(start,start+mid)): count=mid; lo=mid+1
                else: hi=mid-1
            check(count > 0, 'AI_INPUT_LIMIT','A complete record and its required context exceed the batch budget')
            result.append({'start':start,'stop':start+count,
                           'records_sha256':digest(self.records[start:start+count])})
            start += count
        check(0 < len(result) <= self.settings.max_calls,'AI_BATCH_LIMIT')
        return result

    def merge_plan(self, nodes, round_number=1):
        # Sorted semantic descriptors nominate candidates, never decide their
        # membership. Every node is compared once per round. No all-groups POST.
        fields=('label','reason') if round_number%2 else ('reason','label')
        ordered = sorted(nodes,key=lambda n:(n[fields[0]].casefold(),n[fields[1]].casefold(),n['id']))
        groups, current = [], []
        limit = min(self.settings.merge_groups,max(2,self.settings.output_tokens//192))
        # Compare former bucket boundaries in the next pass as well. Candidate
        # ordering is bounded and deterministic, never an equivalence rule.
        shift=min(len(ordered),limit//2) if round_number%2==0 else 0
        ordered=ordered[shift:]+ordered[:shift]
        for node in ordered:
            candidate = current+[node]
            if current and (len(candidate)>limit or not self.fits(self.merge(candidate))):
                groups.append([n['id'] for n in current]); current=[]
            check(self.fits(self.merge([node])), 'AI_INPUT_LIMIT','Complete intermediate group exceeds the reduction budget')
            current.append(node)
        if current: groups.append([n['id'] for n in current])
        check(any(len(g)>1 for g in groups) or len(nodes)==1,'AI_INPUT_LIMIT','No pair of groups fits the reduction budget')
        return groups

    def prepare(self, descriptor, nodes=None):
        prepared=self.leaf(descriptor['start'],descriptor['stop']) if descriptor['kind']=='LEAF' else self.merge(nodes)
        request=self.advisor._request(prepared[0],prepared[1],budget=self.budget)
        return prepared,request

    def execute(self, descriptor, *, nodes=None, prepared_call=None):
        leaf = descriptor['kind']=='LEAF'
        prepared,request = self.prepare(descriptor,nodes) if prepared_call is None else prepared_call
        packet,instruction,version,context = prepared
        check(self.fits(prepared),'AI_INPUT_LIMIT')
        if leaf:
            check(digest(self.records[descriptor['start']:descriptor['stop']])==descriptor['records_sha256'],'AI_INPUT_STALE')
        else:
            check([n['id'] for n in nodes]==descriptor['nodes'],'AI_BATCH_CORRUPT')
        attempt = {'model_called':False}
        try:
            value,metadata = self.advisor._call(packet,instruction,version,attempt,budget=self.budget,prepared_request=request)
            citation_context = self.citation_context(context)
            value,citations = split_reply(value,citation_context)
            self.check_citations(citations)
            advice = validate_clustering_advice(value,context,self.cases,require_rationales=True)
            metadata = model_metadata(metadata,citations,advice,citation_context)
            rationales = {r['group_id']:r for r in advice['group_rationales']}
            cites = {c['target_id']:c for c in citations or []}
            by_id = {n['id']:n for n in nodes or []}
            results = []
            for group in advice['groups']:
                rationale = rationales[group['id']]
                if leaf:
                    def index(key): return descriptor['start']+int(key[1:])
                    members = sorted(index(k) for k in group['record_ids'])
                    selected = [index(k) for k in rationale['evidence_ids']]
                    # Conditions are source facts; bounded summaries retain the
                    # exact set or fail, rather than silently clipping modes.
                    conditions = sorted({str(self.records[i]['fields'].get('mode','')) for i in members})
                else:
                    members = sorted(i for key in group['record_ids'] for i in by_id[key]['members'])
                    selected = list(dict.fromkeys(i for key in rationale['evidence_ids'] for i in by_id[key]['evidence']))[:20]
                    conditions = sorted({v for key in group['record_ids'] for v in by_id[key]['conditions']})
                check(len(members)==len(set(members)), 'INVALID_PARTITION')
                key = 'bg-'+digest([self.context['source_sha256'],members])[:48]
                cite = cites.get(group['id'])
                results.append({'id':key,'label':group['label'],'members':members,'reason':rationale['reason'],
                    'evidence':selected,'references':rationale['reference_groups'],'conditions':conditions,
                    'citation':{**cite,'target_id':key} if cite else None})
            return {'nodes':results,'metadata':metadata,'reason':advice['reason']}
        except ReviewError as error:
            error.model_called = attempt['model_called']
            raise

    def advice(self, nodes):
        groups, rationales, citations, refs = [], [], [], set()
        for node in nodes:
            check(all(type(i)is int and 0<=i<len(self.records) for i in node['members']),'AI_BATCH_CORRUPT')
            groups.append({'id':node['id'],'label':node['label'],'record_ids':[self.records[i]['id'] for i in node['members']]})
            rationales.append({'group_id':node['id'],'reason':node['reason'],
                'evidence_ids':[self.records[i]['id'] for i in node['evidence']], 'reference_groups':node['references']})
            refs.update(r['case_id'] for r in node['references'])
            if node['citation']: citations.append(node['citation'])
        value = {'groups':groups,'group_rationales':rationales,'reference_ids':sorted(refs),
                 'reason':'전체 로그의 배치별 AI 분류와 중간 그룹의 의미 비교를 통합한 제안입니다. 사람이 구성·조건·예외를 검토해 확정해야 합니다.'}
        validate_clustering_advice(value,self.context,self.cases,require_rationales=True)
        return value,citations
