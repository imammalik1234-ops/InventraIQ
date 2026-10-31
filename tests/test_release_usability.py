"""Receiving navigation preserves explicit scope, CSRF and business facts."""
from copy import deepcopy
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from database import db
from scripts.measure_t16 import fixture, client
from simulation.verify import verify
from assistant.evidence import evidence_answer

class ReceivingNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(prefix='inventraiq-release-usability-')
        _, cls.app = fixture(cls.temp.name, 'small')
        cls.before = verify(cls.app)

    @classmethod
    def tearDownClass(cls):
        try:
            if verify(cls.app) != cls.before:
                raise AssertionError('Navigation changed business facts')
        finally:
            with cls.app.app_context():
                db.session.remove()
                db.engine.dispose()
            cls.temp.cleanup()

    def caller(self, uid=901, branch=None):
        caller = client(self.app, uid, branch)
        with caller.session_transaction() as state:
            state['_csrf_token'] = 'release-usability-fixture'
        return caller

    def test_hq_consolidated_receiving_requires_explicit_branch_choice(self):
        caller = self.caller()
        response = caller.get('/receiving')
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn('Choose a branch to receive stock', text)
        self.assertIn('name="next" value="/receiving"', text)
        self.assertIn('value="" disabled selected', text)
        self.assertNotIn('value="all"', text[text.index('<select id="branch-scope"'):text.index('</select>',text.index('<select id="branch-scope"'))])
        with caller.session_transaction() as state:
            self.assertNotIn('active_branch_id', state)

    def test_explicit_selection_returns_to_receiving_with_chosen_branch(self):
        caller = self.caller()
        response = caller.post('/scope', data={'branch_id':'2','next':'/receiving','csrf_token':'release-usability-fixture'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/receiving')
        with caller.session_transaction() as state:
            self.assertEqual(state['active_branch_id'], 2)
        self.assertEqual(caller.get(response.location).status_code, 200)

    def test_manager_default_and_explicit_hq_branch_keep_existing_receiving_forms(self):
        for uid, path in ((912,'/receiving'),(901,'/receiving?branch_id=2')):
            with self.subTest(uid=uid):
                response = self.caller(uid).get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('name="external_reference"',response.get_data(as_text=True))

    def test_analyst_clerk_and_cross_branch_access_still_denied(self):
        for uid,path in ((903,'/receiving'),(922,'/receiving'),(912,'/receiving?branch_id=1')):
            with self.subTest(uid=uid,path=path):
                response = self.caller(uid).get(path)
                self.assertEqual(response.status_code,403)
                self.assertIn('Back to workspace',response.get_data(as_text=True))

    def test_consolidated_write_cannot_use_branch_prompt_to_bypass_service(self):
        with patch('catalog_routes.receiving.receive') as receive:
            response = self.caller().post('/receiving',data={'csrf_token':'release-usability-fixture','quantity':'1'})
            self.assertEqual(response.status_code,403)
            receive.assert_not_called()

    def test_branch_selection_keeps_csrf_and_assignment_checks(self):
        caller = self.caller(912)
        self.assertEqual(caller.post('/scope',data={'branch_id':'2','next':'/receiving'}).status_code,400)
        self.assertEqual(caller.post('/scope',data={'branch_id':'1','next':'/receiving','csrf_token':'release-usability-fixture'}).status_code,403)

    def test_disabled_proposal_browser_pages_explain_return_without_enabling_actions(self):
        self.assertFalse(self.app.config['ASSISTANT_PROPOSALS_ENABLED'])
        with patch('assistant.proposal_routes.proposals.review') as review:
            for uid in (901,903,912,922):
                for path in ('/assistant/proposals','/assistant/proposals/unknown-review'):
                    with self.subTest(uid=uid,path=path):
                        response=self.caller(uid).get(path,headers={'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,image/jxl,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7'})
                        self.assertEqual(response.status_code,503)
                        self.assertEqual(response.mimetype,'text/html')
                        self.assertIn('Assistant action review is switched off.',response.get_data(as_text=True))
                        self.assertIn('Back to workspace',response.get_data(as_text=True))
            review.assert_not_called()
        self.assertFalse(self.app.config['ASSISTANT_PROPOSALS_ENABLED'])

    def test_disabled_proposal_json_and_post_contract_remain_unchanged(self):
        caller=self.caller()
        for headers in ({},{'Accept':'application/json'},{'Accept':'*/*'}):
            response=caller.get('/assistant/proposals',headers=headers)
            self.assertEqual(response.status_code,503)
            self.assertIn('disabled pending schema',response.get_json()['error'])
        response=caller.post('/api/assistant/proposals',json={'question':'unused','request_key':'unused'},headers={'X-CSRF-Token':'release-usability-fixture'})
        self.assertEqual(response.status_code,503)
        self.assertIn('disabled pending schema',response.get_json()['error'])

    def test_header_accessible_names_include_the_visible_button_labels(self):
        from test_design_accessibility import Document
        for uid in (901,903,912,922):
            document=Document(self.caller(uid).get('/dashboard').get_data(as_text=True))
            for attribute,caption in (('data-assistant-open','Ask assistant'),('data-command-open','Go to a workspace')):
                buttons=[node for node in document.elements('button') if attribute in node.attrs and node.attrs.get('aria-label')]
                self.assertEqual(len(buttons),1)
                self.assertIn(caption.lower(),buttons[0].attrs['aria-label'].lower())
                self.assertIn(caption,buttons[0].text)

class EvidenceWordingTests(unittest.TestCase):
    def test_business_wording_preserves_raw_status_quantities_scope_and_citation(self):
        item={'citation':'E1','result':{'status':'ok','tool':'inventory_summary','as_of':'2031-01-03T12:00:00Z','provenance':['synthetic fixture'],
            'scope':{'branch_ids':[2]},'filters':{},'completeness':{'total_rows':1,'truncated':False},
            'data':{'summary':{'on_hand':11,'available':7,'reserved':3,'quarantined':1},'rows':[{'branch_id':2,'sku_id':1,'sku_code':'SYN-REVIEW','lot_code':'SYN-BATCH','on_hand':11,'available':7,'status':'not_tracked'}]}}}
        original=deepcopy(item)
        answer=evidence_answer([item])
        self.assertEqual(item,original)
        self.assertTrue(answer['coverage_complete'])
        self.assertIn('11 physical units, 7 available, 3 reserved and 1 quarantined',answer['findings'][0]['text'])
        row=answer['findings'][1]
        self.assertIn('expiry date not tracked',row['text'])
        self.assertNotIn('not_tracked',row['text'])
        self.assertEqual(row['branch_id'],2)
        self.assertEqual(row['citations'],['E1'])
        self.assertNotIn('T25',answer['disclosure'])

if __name__ == '__main__':
    unittest.main()
