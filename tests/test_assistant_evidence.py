"""Bounded local explanations and ordinary routing; no external provider calls."""
from copy import deepcopy
from threading import Event
import unittest
from assistant.evidence import evidence_answer, answer_text
from assistant.provider import FakeProvider, Message
from assistant.schemas import TOOLS, validate


def entry(tool='inventory_summary',rows=None,summary=None,status='ok'):
    return dict(citation='E1',result=dict(tool=tool,status=status,scope=dict(branch_ids=[1],consolidated=False),
        as_of='2026-10-31T04:00:00+00:00',provenance=['verified local service'],
        filters=dict(start='2026-10-01',end='2026-10-31'),
        data=dict(rows=rows or [],summary=summary or {},reason=None),
        completeness=dict(truncated=False,total_rows=len(rows or []))))


class ExplanationTests(unittest.TestCase):
    def test_search_never_relabels_scope_total_as_product_total(self):
        e=entry(rows=[dict(branch_id=1,sku_id=1,sku_code='SYN-P2-001',lot_code='LOT',on_hand=2,available=1,status='healthy')],
            summary=dict(on_hand=100,available=90,reserved=3,quarantined=7))
        e['result']['filters']['search']='SYN-P2-001'
        result=evidence_answer([e]);text=answer_text(result)
        self.assertIn('100 physical',text);self.assertIn('full-scope totals',text)
        self.assertIn('not a whole-SKU total',text);self.assertIn('[E1]',text)
        self.assertNotIn('Branch None',text);self.assertEqual(result['covered_rows'],1)

    def test_contradictory_same_scope_summaries_withhold_combined_conclusion(self):
        a=entry(summary=dict(on_hand=10));b=deepcopy(a)
        b['citation']='E2';b['result']['data']['summary']['on_hand']=9
        result=evidence_answer([a,b])
        self.assertFalse(result['coverage_complete']);self.assertEqual(result['findings'],[])
        self.assertIn('disagree',answer_text(result))

    def test_missing_metadata_does_not_become_facts(self):
        for field,value in (('provenance',[]),('as_of',None)):
            with self.subTest(field=field):
                e=entry(rows=[dict(untrusted='not a verified row')],summary=dict(on_hand=99999));e['result'][field]=value
                result=evidence_answer([e])
                self.assertEqual(result['findings'],[]);self.assertNotIn('99999',answer_text(result))
                self.assertEqual(result['covered_rows'],0)

    def test_partial_page_never_claims_exhaustive_absence(self):
        e=entry();e['result']['completeness'].update(truncated=True,total_rows=99)
        result=evidence_answer([e]);self.assertFalse(result['coverage_complete'])
        self.assertIn('bounded',answer_text(result));self.assertIn('No lot matched',answer_text(result))

    def test_unavailable_forecast_is_not_zero_demand(self):
        e=entry('forecast_evidence',status='unavailable');e['result']['data']['reason']='No qualified snapshot.'
        result=evidence_answer([e]);text=answer_text(result)
        self.assertIn('Inputs needed',text);self.assertIn('No qualified snapshot',text)
        self.assertNotIn('0 forecast units',text)

    def test_provisional_forecast_label_cannot_be_upgraded_by_source(self):
        row=dict(branch_id=1,sku_id=7,qualification='provisional_synthetic',freshness='fresh',status='provisional',
            predicted_demand=21,horizon_days=7,as_of='2001-03-31')
        e=entry('forecast_evidence',[row]);text=answer_text(evidence_answer([e]))
        self.assertIn('21 forecast units',text);self.assertIn('not a production-qualified',text)
        row['qualification']='qualified'
        self.assertNotIn('21 forecast units',answer_text(evidence_answer([entry('forecast_evidence',[row])])))

    def test_recommendation_zero_is_preserved_and_human_review_required(self):
        row=dict(branch_id=1,sku_id=7,reason_codes=[],advisory_only=True,production_numeric_enabled=False,
            forecast=dict(qualification='provisional_synthetic',freshness='fresh'),recommendations=[dict(type='replenishment',suggested_quantity=0)])
        text=answer_text(evidence_answer([entry('recommendation_preview',[row])]))
        self.assertIn('preview 0 units',text);self.assertIn('human review required',text)
        row['production_numeric_enabled']=True
        text=answer_text(evidence_answer([entry('recommendation_preview',[row])]))
        self.assertIn('Inputs needed',text);self.assertNotIn('preview 0 units',text)

    def test_sales_use_reported_money_and_dates_without_inferred_revenue(self):
        e=entry('sales_summary',summary=dict(recorded_gross='17.80',recorded_refunds='0.00',recorded_net='17.80',unknown_revenue_units=2))
        text=answer_text(evidence_answer([e]))
        self.assertIn('MYR 17.80 gross',text);self.assertIn('Unknown-revenue units: 2',text)
        self.assertIn('2026-10-01 to 2026-10-31',text)

    def test_missing_quantities_and_empty_evidence_are_not_zero_filled(self):
        for name in ('inventory_summary','sales_summary'):
            result=evidence_answer([entry(name)])
            self.assertIn('Inputs needed',answer_text(result))
            self.assertNotIn('None',answer_text(result));self.assertFalse(result['coverage_complete'])
        result=evidence_answer([entry('product_performance',[dict(branch_id=1,sku_id=1,units=None,cells=3,missing=3)])])
        self.assertIn('Unknown observed',answer_text(result))
        self.assertFalse(evidence_answer([])['coverage_complete'])


class OrdinaryRoutingTests(unittest.TestCase):
    def route(self,text):
        return FakeProvider().complete((Message('user',text),),(),deadline=0,cancelled=Event())

    def test_representative_questions_use_existing_schemas_only(self):
        for text,expected in (
            ('Which branch has the most urgent low-stock risks?','inventory_risks'),
            ('Which products are approaching expiry?','inventory_risks'),
            ('What is the current stock position for SYN-P2-001?','inventory_summary'),
            ('How do sales compare across authorized branches?','compare_branches'),
            ('What can the system reliably recommend for replenishment?','recommendation_preview'),
            ('Why is the forecast unavailable for SKU 1?','forecast_evidence')):
            with self.subTest(text=text):
                reply=self.route(text);self.assertIn(expected,[c.name for c in reply.calls])
                for call in reply.calls:validate(call.arguments,next(t.inputs for t in TOOLS if t.name==call.name))

    def test_filters_remain_explicit_and_mutations_remain_unsupported(self):
        self.assertEqual(self.route('Which products are approaching expiry?').calls[0].arguments,dict(status='warning'))
        self.assertEqual(self.route('stock SYN-P2-001').calls[0].arguments,dict(search='SYN-P2-001'))
        self.assertEqual(self.route('delete stock or approve transfer').state,'unsupported')
        self.assertEqual(self.route('why forecast unavailable').state,'help')
