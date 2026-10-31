"""Deterministic coverage, truthful explanation and unchanged read boundaries."""
from copy import deepcopy
import hashlib
import os
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from assistant import conversations, orchestrator, tools
from assistant.evidence import risk_answer, risk_page
from assistant.gemini import INVENTORY_URGENCY_QUESTION, Settings, payload_for
from assistant.provider import Reply, ToolCall
from database import db
from scripts.measure_t16 import fixture, client


class FixedPlan:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        return self.reply


class TransformationEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(prefix='inventraiq-evidence-')
        with patch.dict(os.environ, {'ASSISTANT_PROVIDER':'fake'}):
            _, cls.app = fixture(cls.temp.name, 'small')
        from simulation.verify import verify
        cls.before = verify(cls.app)

    @classmethod
    def tearDownClass(cls):
        from simulation.verify import verify
        try:
            if verify(cls.app) != cls.before:
                raise AssertionError('Evidence presentation changed business facts')
        finally:
            with cls.app.app_context():
                db.session.remove()
                db.engine.dispose()
            cls.temp.cleanup()

    def setUp(self):
        self.app.extensions.pop('inventraiq_t26_history', None)
        self.app.extensions.pop('inventraiq_t26_provider', None)
        self.env = patch.dict(os.environ, {'ASSISTANT_PROVIDER':'fake'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def send(self, uid=903, branch=None, reply=None, question=INVENTORY_URGENCY_QUESTION):
        caller = client(self.app, uid, branch)
        with caller.session_transaction() as state:
            state['_csrf_token'] = 'transformation-test'
        headers = {'X-CSRF-Token':'transformation-test'}
        created = caller.post('/api/assistant/conversations', json={}, headers=headers)
        self.assertEqual(created.status_code, 201, created.get_json())
        if reply is not None:
            self.app.extensions['inventraiq_t26_provider'] = FixedPlan(reply)
        key = created.get_json()['id']
        result = caller.post('/api/assistant/conversations/'+key+'/messages',
            json={'text':question}, headers=headers)
        caller.post('/api/assistant/conversations/'+key+'/forget', json={}, headers=headers)
        return result

    def test_exact_hq_question_covers_risks_without_synthetic_facts_in_prompt(self):
        reply = Reply((ToolCall('a', 'inventory_risks', {'branch_id':2,'status':'warning','page_size':1}),))
        with patch.object(tools, 'invoke', wraps=tools.invoke) as dispatch:
            response = self.send(reply=reply)
        self.assertEqual(response.status_code, 200, response.get_json())
        turn = response.get_json()['messages'][-1]
        self.assertEqual(dispatch.call_args_list[0].args, ('inventory_risks', {'page':1,'page_size':25}))
        self.assertEqual(turn['usage']['tool_calls'], 1)
        self.assertEqual(self.app.extensions['inventraiq_t26_provider'].calls, 1)
        answer = turn['answer']
        self.assertTrue(answer['coverage_complete'])
        self.assertFalse(answer['ranked'])
        self.assertTrue(any(r['branch_id']==2 and r['condition']=='Low stock' for r in answer['findings']))
        self.assertTrue(any(r['branch_id']==1 and r['condition']=='Near expiry' and '1 day remaining' in r['text'] for r in answer['findings']))
        self.assertIn('deterministic', answer['disclosure'])
        self.assertIn('no approved rule', turn['text'])
        self.assertIn('[E1]', turn['text'])
        payload = conversations.encoded(payload_for(INVENTORY_URGENCY_QUESTION, Settings(output_tokens=256)))
        self.assertEqual(len(payload), 5752)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), '7b191b9833cc169b30cfded581938dcbfa9c40c976f61d7719a4f9a546aa6ece')

    def test_branch_actor_coverage_cannot_expand_to_hq(self):
        result = self.send(uid=912, branch=2)
        self.assertEqual(result.status_code, 200, result.get_json())
        turn = result.get_json()['messages'][-1]
        self.assertEqual(turn['evidence'][0]['result']['scope']['branch_ids'], [2])
        self.assertTrue(all(row['branch_id']==2 for row in turn['answer']['findings']))

    def test_invalid_foreign_duplicate_or_write_plan_is_rejected_before_coverage(self):
        cases = [
            (Reply((ToolCall('a','stock.adjust',{}),)), 'unsupported_tool'),
            (Reply((ToolCall('a','inventory_risks',{'role':'hq_admin'}),)), 'invalid_arguments'),
            (Reply((ToolCall('a','inventory_risks',{'branch_id':1}),)), 'access_denied'),
            (Reply((ToolCall('a','inventory_summary',{}),ToolCall('a','inventory_risks',{}))), 'invalid_tool_call'),
            (Reply((ToolCall('a','inventory_risks',{}),ToolCall('b','inventory_risks',{}))), 'invalid_tool_call')]
        for reply, code in cases:
            with self.subTest(code=code), patch.object(orchestrator,'execute_tool') as execute:
                response = self.send(uid=912, branch=2, reply=reply)
                self.assertGreaterEqual(response.status_code, 400)
                self.assertEqual(response.get_json()['error']['code'], code)
                execute.assert_not_called()

    def test_cancelled_plan_never_runs_coverage(self):
        with patch.object(orchestrator,'execute_tool') as execute:
            result = self.send(reply=Reply(state='cancelled'))
        self.assertEqual(result.status_code, 409)
        execute.assert_not_called()

    def test_t25_denial_stays_unusable_and_is_never_claimed_as_zero_risk(self):
        with patch.object(tools,'invoke',return_value=tools._failure('inventory_risks','access_denied','Access denied.')):
            response = self.send()
        self.assertEqual(response.status_code, 200)
        answer = response.get_json()['messages'][-1]['answer']
        self.assertFalse(answer['coverage_complete'])
        self.assertEqual(answer['findings'], [])
        self.assertIn('could not be established', answer['summary'])

    def test_budgeted_pagination_uses_signed_snapshot_and_stops_at_four(self):
        first = self.send().get_json()['messages'][-1]['evidence'][0]
        template = deepcopy(first['result'])
        original = {key:value for key,value in template['data']['rows'][0].items()
            if key in ('branch_id','sku_id','on_hand','available','status','threshold','lot_id','source')}
        def pages(name, arguments):
            page = arguments['page']
            self.assertEqual(name, 'inventory_risks')
            self.assertEqual(arguments['page_size'], 25)
            if page > 1:
                self.assertEqual(arguments['snapshot'], 'signed-optimistic-snapshot')
            result = deepcopy(template)
            result['data']['rows'] = [dict(original, sku_id=page*100+i) for i in range(25)]
            result['completeness'].update(total_rows=101,returned_rows=25,page=page,page_size=25,
                truncated=True,continuation='signed-optimistic-snapshot')
            return result
        self.app.extensions.pop('inventraiq_t26_history', None)
        with patch.object(tools,'invoke',side_effect=pages) as execute:
            response = self.send(reply=Reply((ToolCall('a','inventory_summary',{}),)))
        self.assertEqual(response.status_code, 200, response.get_json())
        turn = response.get_json()['messages'][-1]
        self.assertEqual(execute.call_count, 4)
        self.assertEqual(turn['usage']['tool_calls'], 4)
        self.assertEqual(turn['answer']['covered_rows'], 100)
        self.assertFalse(turn['answer']['coverage_complete'])
        self.assertEqual(self.app.extensions['inventraiq_t26_provider'].calls, 1)

    def test_complete_multi_page_observations_remain_local_and_cited(self):
        original = self.send().get_json()['messages'][-1]['evidence'][0]
        entries = []
        row = original['result']['data']['rows'][0]
        for page, count in ((1,25),(2,3)):
            entry = deepcopy(original)
            entry['citation'] = 'E'+str(page)
            entry['result']['data']['rows'] = [dict(row,sku_id=page*100+i) for i in range(count)]
            entry['result']['completeness'].update(total_rows=28,returned_rows=count,
                truncated=True,page=page,continuation='signed')
            entries.append(entry)
        answer = risk_answer(entries, (1,2,3))
        self.assertTrue(answer['coverage_complete'])
        self.assertEqual(answer['covered_rows'], 28)
        self.assertEqual(risk_page(entries,4), None)

    def test_missing_source_or_foreign_rows_do_not_support_explanation(self):
        original = self.send().get_json()['messages'][-1]['evidence'][0]
        for field, value in (('provenance',[]),('as_of',None),('scope',dict(branch_ids=[4]))):
            with self.subTest(field=field):
                entry=deepcopy(original);entry['result'][field]=value
                answer=risk_answer([entry],(1,2,3))
                self.assertFalse(answer['coverage_complete'])
                self.assertEqual(answer['findings'],[])

    def test_original_question_variants_do_not_gain_hidden_coverage_reads(self):
        response = self.send(question=INVENTORY_URGENCY_QUESTION+' ',reply=Reply())
        self.assertEqual(response.status_code,200)
        self.assertNotIn('retrieval_policy',response.get_json()['messages'][-1])


if __name__ == '__main__':
    unittest.main()
