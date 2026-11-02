"""Small display-only counters; never scan child history on a web poll."""


def count(value):
    return value if type(value) is int and 0 <= value <= 1000000 else None


def group_count_after(event, state, descriptor, produced):
    if event['data']['command']['group_id'] is not None:
        return 1
    kind = descriptor['kind']
    if kind == 'LEAF':
        before = count(state.get('current_groups', state.get('nodes')))
        return count(before + produced) if before is not None else None
    if kind == 'MERGE':
        before = count(state.get('current_groups'))
        inputs = descriptor.get('node_refs')
        if before is not None and isinstance(inputs,list):
            return count(before - len(inputs) + produced)
    return None


def display_metrics(event, state=None, *, phase=None):
    data = event['data']
    review = data['command']['group_id'] is not None
    if state is None:
        groups = (data.get('result') or {}).get('advice',{}).get('groups')
        total = 1 if review else count(len(groups)) if isinstance(groups,list) else None
        return {'merge_iteration':None if 'batch' in data else 0, 'current_group_count':total}
    phase = phase or state.get('phase', 'MERGE' if state.get('round',0) else 'CLASSIFY')
    iteration = state.get('stage_round',state.get('round')) if phase in ('MERGE','REVIEW_MERGE','TEXT_MERGE') else 0
    total = 1 if review else count(state.get('current_groups'))
    if total is None and phase == 'CLASSIFY':
        total = count(state.get('nodes'))
    if total is None and phase == 'MERGE':
        # Old checkpoints lack an incremental counter. Their final output is
        # known when every descriptor finishes; partial old merges stay unknown.
        done = state.get('index',0) + sum(s.get('status')=='DONE' for s in state.get('wave',[]))
        if done == state.get('round_total'):
            total = count(state.get('nodes'))
    return {'merge_iteration':count(iteration), 'current_group_count':total}
