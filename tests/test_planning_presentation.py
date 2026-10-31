"""Planning presentation never invents quantities or changes business rules."""
import unittest
from services.planning_presentation import branch_name,missing_inputs,review_cards
from assistant.provider import FakeProvider,Message
from threading import Event


class PlanningPresentationTests(unittest.TestCase):
    def test_default_names_are_friendly_but_custom_names_are_retained(self):
        self.assertEqual(branch_name('Demo Central'),'Central')
        self.assertEqual(branch_name('DEMO-02'),'Eastside')
        self.assertEqual(branch_name('Demo West'),'Westside')
        self.assertEqual(branch_name('Harbour Outlet'),'Harbour Outlet')

    def test_missing_forecasts_have_actionable_explanations_not_zero_orders(self):
        row=dict(branch_id=2,branch_code='DEMO-02',sku_id=7,sku_code='RICE',inputs=dict(available_stock=5),calculation=None,
                 reason_codes=['FORECAST_UNAVAILABLE','FORECAST_STALE','LOT_OR_OPENING_REVIEW_REQUIRED'])
        card=review_cards(dict(items=[row]),{'2':'Eastside'})[0]
        self.assertIsNone(card['quantity'])
        self.assertFalse(card['ready'])
        self.assertEqual(card['branch'],'Eastside')
        self.assertEqual(len(card['reasons']),2)
        self.assertIn('forecast',card['reasons'][0])

    def test_complete_review_keeps_the_exact_policy_calculation_including_zero(self):
        for quantity in (0,24):
            row=dict(branch_id=1,branch_code='DEMO-01',sku_id=7,sku_code='RICE',inputs=dict(available_stock=5),reason_codes=[],
                     calculation=dict(purchase_quantity=quantity,potential_excess=3,demand='16.5'))
            card=review_cards(dict(items=[row]),{'1':'Central'})[0]
            self.assertEqual(card['quantity'],quantity)
            self.assertTrue(card['ready'])
            self.assertEqual(card['demand'],'16.5')

    def test_analytics_assistant_reads_use_the_requested_period(self):
        reply=FakeProvider().complete((Message('user','inventory and sales 2030-01-03 to 2031-01-03'),),(),deadline=0,cancelled=Event())
        self.assertEqual([call.name for call in reply.calls],['inventory_summary','sales_summary'])
        self.assertTrue(all(call.arguments==dict(start='2030-01-03',end='2031-01-03') for call in reply.calls))

    def test_partial_dates_prompt_for_clarification(self):
        reply=FakeProvider().complete((Message('user','sales from 2030-01-03'),),(),deadline=0,cancelled=Event())
        self.assertEqual(reply.state,'help')
        self.assertEqual(reply.calls,())

    def test_page_branch_hint_is_bounded_and_explicit_question_takes_precedence(self):
        provider=FakeProvider()
        messages=(Message('context','{"branch_id":2}'),Message('user','recommendations'))
        reply=provider.complete(messages,(),deadline=0,cancelled=Event())
        self.assertEqual(reply.calls[0].arguments['branch_id'],2)
        reply=provider.complete((messages[0],Message('user','recommendations branch 3')),(),deadline=0,cancelled=Event())
        self.assertEqual(reply.calls[0].arguments['branch_id'],3)
        reply=provider.complete((Message('context','{"branch_id":2,"role":"hq_admin"}'),messages[1]),(),deadline=0,cancelled=Event())
        self.assertNotIn('branch_id',reply.calls[0].arguments)

    def test_contextual_recommendation_keeps_the_exact_branch_and_product(self):
        reply=FakeProvider().complete((Message('user','recommendations branch 2 SKU 7'),),(),deadline=0,cancelled=Event())
        self.assertEqual(reply.calls[0].name,'recommendation_preview')
        self.assertEqual(reply.calls[0].arguments,dict(branch_id=2,sku_id=7))
