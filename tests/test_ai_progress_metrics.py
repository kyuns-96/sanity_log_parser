"""Small, incremental group counters and stage-local iteration display."""
import unittest

from oa.sanity_ai_progress import display_metrics, group_count_after


def event(group=None, result=None):
    return {'data':{'command':{'group_id':group}, **({'result':result} if result else {})}}


class ProgressMetricsTests(unittest.TestCase):
    def test_leaf_counts_committed_candidates_and_merge_replaces_only_its_inputs(self):
        job=event()
        self.assertEqual(3,group_count_after(job,{'current_groups':1},{'kind':'LEAF'},2))
        self.assertEqual(8,group_count_after(job,{'current_groups':10},{'kind':'MERGE','node_refs':[1,2,3]},1))
        self.assertEqual(10,group_count_after(job,{'current_groups':10},{'kind':'MERGE','node_refs':[1]},1))
        self.assertIsNone(group_count_after(job,{'current_groups':1},{'kind':'MERGE','node_refs':[1,2,3]},1))

    def test_legacy_leaf_uses_committed_nodes_and_partial_legacy_merge_stays_unknown(self):
        self.assertEqual(5,group_count_after(event(),{'nodes':3},{'kind':'LEAF'},2))
        state={'round':2,'phase':'MERGE','round_total':4,'index':1,'nodes':2}
        self.assertEqual({'merge_iteration':2,'current_group_count':None},display_metrics(event(),state))
        state.update(index=3,wave=[{'status':'DONE'}])
        self.assertEqual(2,display_metrics(event(),state)['current_group_count'])

    def test_stage_iteration_uses_stage_round_and_group_review_keeps_one_group(self):
        for phase in ('MERGE','REVIEW_MERGE','TEXT_MERGE'):
            with self.subTest(phase=phase):
                values=display_metrics(event('selected'),{'round':9,'stage_round':2,'phase':phase,'current_groups':99})
                self.assertEqual({'merge_iteration':2,'current_group_count':1},values)
        self.assertEqual(1,group_count_after(event('selected'),{}, {'kind':'REVIEW_LEAF'},20))

    def test_text_analysis_has_no_candidate_groups_and_classification_restarts_iteration(self):
        self.assertEqual({'merge_iteration':3,'current_group_count':None},display_metrics(event(),
            {'phase':'TEXT_MERGE','round':3,'stage_round':3,'nodes':20}))
        self.assertIsNone(group_count_after(event(),{}, {'kind':'TEXT'},20))
        self.assertEqual({'merge_iteration':0,'current_group_count':0},display_metrics(event(),
            {'phase':'CLASSIFY','round':4,'stage_round':0,'current_groups':0}))

    def test_single_call_uses_its_actual_groups_and_invalid_optional_counts_are_hidden(self):
        self.assertEqual({'merge_iteration':0,'current_group_count':2},display_metrics(event(result={'advice':{'groups':[{},{}]}})))
        self.assertEqual({'merge_iteration':0,'current_group_count':None},display_metrics(event()))
        for bad in (True,-1,1000001,float('nan'),'2'):
            with self.subTest(bad=bad):
                values=display_metrics(event(),{'phase':'MERGE','round':bad,'current_groups':bad})
                self.assertEqual({'merge_iteration':None,'current_group_count':None},values)
