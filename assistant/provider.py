"""Stage A protocol and LOCAL deterministic demonstration. No network client.

Adapters return plans, never authoritative prose. Only the server's evidence
renderer may present facts. Context and tool payloads are always untrusted data.
"""
from dataclasses import dataclass
import json
import re
from typing import Literal, Protocol


@dataclass(frozen=True)
class Message:
    role: Literal['system','context','user','assistant','tool']
    content: str
    untrusted: bool = True


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class Reply:
    calls: tuple[ToolCall, ...] = ()
    # No arbitrary model-authored claims, HTML, URLs, confidence or citations.
    state: Literal['evidence','help','unsupported','cancelled'] = 'evidence'


class Provider(Protocol):
    def complete(self, messages: tuple[Message, ...], catalog: tuple[dict, ...],
                 *, deadline: float, cancelled) -> Reply:
        """Cooperate with cancellation/deadline; never execute tools directly."""
        ...


class FakeProvider:
    """Fixed local routing, not an AI model or a live forecast generator."""
    def propose(self,question):
        from assistant.proposal_provider import fake_intent
        return fake_intent(question)

    def complete(self, messages, catalog, *, deadline, cancelled):
        if cancelled.is_set():return Reply(state='cancelled')
        if messages[-1].role == 'tool':return Reply()
        text=next(m.content for m in reversed(messages) if m.role=='user').lower()
        if any(word in text for word in ('ignore ', 'system prompt', 'password', 'secret', 'impersonat',
                                         'pretend ', 'act as', 'sql', 'delete ', 'approve ', 'transfer ',
                                         'purchase ', 'create sale', 'adjust stock', 'role:')):
            return Reply(state='unsupported')
        args={}
        branch=re.search(r'\bbranch\s+(\d{1,10})\b',text)
        if branch:args['branch_id']=int(branch[1])
        else:
            # A page can suggest its selected branch. The dispatcher still
            # validates arguments and rechecks authorization before any read.
            context=next((m.content for m in reversed(messages) if m.role=='context'),'')
            try:hint=json.loads(context)
            except (ValueError,TypeError):hint=None
            if type(hint) is dict and set(hint)=={'branch_id'} and type(hint['branch_id']) is int and hint['branch_id']>0:
                args['branch_id']=hint['branch_id']
        names=[]
        if 'forecast' in text:
            sku=re.search(r'\bsku\s+(\d{1,10})\b',text)
            if not sku:return Reply(state='help')
            names=['forecast_evidence'];args['sku_id']=int(sku[1])
        elif 'recommendation' in text or 'replenish' in text or 'reorder' in text:
            names=['recommendation_preview']
            sku=re.search(r'\bsku\s+(\d{1,10})\b',text)
            if sku:args['sku_id']=int(sku[1])
        elif 'performance' in text:
            dates=re.findall(r'\b\d{4}-\d{2}-\d{2}\b',text)
            if len(dates)!=2:return Reply(state='help')
            names=['product_performance'];args.update(start=dates[0],end=dates[1])
        elif 'csv' in text:
            names=['report_artifact'];args.update(source='inventory_summary',format='csv')
        elif 'chart' in text:
            names=['report_artifact'];args.update(source='compare_branches',format='chart',metric='on_hand')
        else:
            if 'inventory' in text or 'stock' in text:names.append('inventory_summary')
            if 'sales' in text:names.append('sales_summary')
            if any(word in text for word in ('risk','alert','expiry','expir')):names.append('inventory_risks')
            if 'compare' in text:names.append('compare_branches')
            if 'inventory_risks' in names:
                # A risk list, rather than a stock page, answers these conditions.
                if 'inventory_summary' in names:names.remove('inventory_summary')
                if 'low-stock' in text or 'low stock' in text:args['status']='low_stock'
                elif 'approaching expiry' in text or 'near expiry' in text or 'near-expiry' in text:args['status']='warning'
                if 'compare_branches' in names:names.remove('compare_branches')
            code=re.search(r'\bsyn-p2-\d{3}\b',text)
            if code and names==['inventory_summary']:args['search']=code[0].upper()
        if not names:return Reply(state='help')
        dates=re.findall(r'\b\d{4}-\d{2}-\d{2}\b',text)
        if dates and set(names)<=set(('inventory_summary','sales_summary','inventory_risks','compare_branches','product_performance')):
            if len(dates)!=2:return Reply(state='help')
            args.update(start=dates[0],end=dates[1])
        return Reply(tuple(ToolCall(str(i),name,{k:v for k,v in args.items()
            if k not in ('status','search') or name in ('inventory_summary','inventory_risks')}) for i,name in enumerate(names)))
