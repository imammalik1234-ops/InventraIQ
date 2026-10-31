"""Server-defined risk coverage and deterministic, locally grounded presentation.

No provider prose, external synthesis, priority score or business write belongs
here. The dispatcher remains the authority for every read, including pages.
"""
from collections import defaultdict
from assistant.provider import ToolCall


def inventory_urgency(question):
    from assistant.gemini import INVENTORY_URGENCY_QUESTION
    return question == INVENTORY_URGENCY_QUESTION


def risk_page(evidence, maximum):
    """Next bounded, unfiltered page; reuse the signed optimistic snapshot.

    A known cross-branch question requires coverage of the authenticated scope,
    rather than relying on a planner's optional branch/status/search filters.
    Never turn an error, missing continuation or exhausted budget into success.
    """
    page = len(evidence) + 1
    if page > maximum:
        return None
    arguments = dict(page=page, page_size=25)
    if evidence:
        result = evidence[-1]['result']
        complete = result.get('completeness', {})
        if result.get('status') != 'ok':
            return None
        if complete.get('total_rows') is None or page * 25 - 25 >= complete['total_rows']:
            return None
        snapshot = complete.get('continuation')
        if not snapshot:
            return None
        arguments['snapshot'] = snapshot
    return ToolCall('server-risk-page-' + str(page), 'inventory_risks', arguments)


def risk_answer(evidence, branches):
    """Explain observed conditions with citations, withholding a priority winner.

    Names/quantities come only from validated T25 output. A repeated row is
    described once. Partial pages may support observations, never an exhaustive
    statement about a branch or the absence of another condition.
    """
    groups = defaultdict(list)
    seen = set()
    covered = 0
    expected = None
    sources_ok = bool(evidence)
    for item in evidence:
        result = item['result']
        scope = result.get('scope') or {}
        data = result.get('data') or {}
        rows = data.get('rows', [])
        valid = (result.get('tool') == 'inventory_risks' and result.get('status') == 'ok'
            and set(scope.get('branch_ids', [])) == set(branches)
            and result.get('as_of') and result.get('provenance')
            and all(row.get('branch_id') in branches for row in rows))
        if not valid:
            sources_ok = False
            continue
        complete = result.get('completeness', {})
        total = complete.get('total_rows')
        if expected is None:
            expected = total
        if total != expected:
            sources_ok = False
        covered += len(rows)
        for row in rows:
            key = (row.get('branch_id'), row.get('sku_id'), row.get('lot_id'), row.get('status'), row.get('source'))
            if key in seen:
                continue
            seen.add(key)
            groups[row['branch_id']].append((row, item['citation']))
    complete = bool(sources_ok and expected is not None and covered == expected)
    findings = []
    for branch, entries in sorted(groups.items()):
        for row, citation in entries:
            product = row.get('sku_code') or 'SKU ' + str(row['sku_id'])
            status = row.get('status')
            if status == 'low_stock':
                description = f"{product}: {row['available']} available units; reorder threshold {row.get('threshold', 'unavailable')}."
                kind = 'Low stock'
            elif status in ('critical', 'high', 'early'):
                days = row.get('days_remaining')
                horizon = (f'{days} day' + ('' if days == 1 else 's') + ' remaining') if days is not None else 'expiry horizon unavailable'
                description = f"{product}, lot {row.get('lot_code') or row.get('lot_id')}: {row['on_hand']} physical units; {horizon}."
                kind = 'Near expiry'
            elif status == 'expired':
                description = f"{product}, lot {row.get('lot_code') or row.get('lot_id')}: {row['on_hand']} physical units classified expired."
                kind = 'Expired stock'
            elif status == 'review':
                description = f"{product}: {row['on_hand']} physical units require source or policy review."
                kind = 'Review required'
            else:
                continue
            findings.append(dict(branch_id=branch, condition=kind, text=description, citations=[citation]))
    headline = ('The authorized inventory evidence shows the issues below.' if findings else
        'No inventory conditions were found in the verified results.' if complete else
        'Inventory evidence could not be established completely.')
    limitations = ['A single priority branch is not established: no approved rule ranks stock shortages against expiry exposure.']
    if not complete:
        limitations.append('Coverage is incomplete. Missing, rejected or budget-limited results must not be interpreted as an absence of risk.')
    return dict(kind='deterministic_local_evidence', title='Inventory issues to review',
        summary=headline, findings=findings, coverage_complete=complete,
        covered_rows=covered, total_rows=expected, ranked=False,
        limitations=limitations,
        disclosure='Prepared locally from records available to your account. This is a deterministic explanation, not model-generated advice.')


def answer_text(answer):
    lines = [answer['summary']]
    lines.extend((f"Branch {row['branch_id']} — " if row['branch_id'] is not None else '')+
        f"{row['condition']}: {row['text']} [{', '.join(row['citations'])}]" for row in answer['findings'])
    lines.extend(answer['limitations'])
    return '\n'.join(lines)


def expiry_description(status):
    """Business wording for existing expiry statuses; source values are unchanged."""
    return {'not_tracked': 'expiry date not tracked', 'healthy': 'within recorded expiry limits',
            'critical': 'near expiry; urgent review', 'high': 'near expiry; high priority',
            'early': 'approaching expiry', 'expired': 'expired',
            'review': 'expiry details need review'}.get(status, str(status).replace('_', ' '))


def evidence_answer(evidence):
    """Explain validated local tool values, never provider-authored assertions.

    Each finding retains its own scope, source and clock. Bounded lists cannot
    prove absence, priority or whole-SKU totals. Disagreeing summary snapshots
    with the same scope/date filters withhold a combined answer.
    """
    findings=[];limitations=[];summaries={};conflict=False;complete=bool(evidence);verified_rows=0
    def add(item,text,branch=None):
        result=item['result']
        findings.append(dict(branch_id=branch,condition=result['tool'],text=text,
            citations=[item['citation']]))
    for item in evidence:
        r=item['result'];data=r.get('data') or {};scope=r.get('scope') or {}
        branches=scope.get('branch_ids',[]);name=r.get('tool');rows=data.get('rows',[])
        source=r.get('provenance') or []
        if r.get('status')=='error' or not source or not branches or (r.get('status')=='ok' and not r.get('as_of')):
            complete=False
            limitations.append('A requested source was unavailable or denied; no facts are inferred from that result.')
            continue
        if r.get('status')=='ok':verified_rows+=len(rows)
        filters=r.get('filters') or {};summary=data.get('summary') or {}
        identity=(tuple(branches),filters.get('start'),filters.get('end'))
        previous=summaries.setdefault(identity,{})
        for key,value in summary.items():
            if key in previous and previous[key]!=value:conflict=True
            previous[key]=value
        coverage=r.get('completeness') or {}
        if coverage.get('truncated') or coverage.get('total_rows') is None:
            complete=False
            limitations.append('Displayed rows are bounded; they do not establish exhaustive coverage or an absence of other risks.')
        if r.get('status')=='unavailable':
            complete=False
            add(item,'Inputs needed. '+(data.get('reason') or 'No current compatible evidence is available.'))
            continue
        if name in ('inventory_summary','sales_summary','compare_branches'):
            if name=='inventory_summary':
                if all(summary.get(k) is not None for k in ('on_hand','available','reserved','quarantined')):
                    add(item,f"Across the selected branches: {summary['on_hand']} physical units, {summary['available']} available, {summary['reserved']} reserved and {summary['quarantined']} quarantined. These are full-scope totals; a search filters displayed lots only.")
                else:
                    complete=False;add(item,'Inputs needed; one or more stock summary quantities are unavailable.')
                for row in rows[:5]:
                    add(item,f"{row.get('sku_code') or 'SKU '+str(row['sku_id'])}, lot {row.get('lot_code') or row.get('lot_id')}: {row['on_hand']} physical and {row['available']} available units; expiry status: {expiry_description(row['status'])}. This is one batch balance, not the product total across all batches in this branch.",row['branch_id'])
            elif name=='sales_summary':
                if all(summary.get(k) is not None for k in ('recorded_gross','recorded_refunds','recorded_net','unknown_revenue_units')):
                    add(item,f"Authorized scope {branches}, inclusive {filters.get('start')} to {filters.get('end')}: recorded MYR {summary['recorded_gross']} gross, {summary['recorded_refunds']} refunds and {summary['recorded_net']} net. Refunds use their own business dates. Unknown-revenue units: {summary['unknown_revenue_units']}; no revenue is inferred for them.")
                else:
                    complete=False;add(item,'Inputs needed; recorded sales or refund summary values are unavailable. No revenue is inferred.')
            else:
                for row in rows:
                    add(item,f"{row.get('code') or 'Authorized branch'}: {row.get('on_hand')} physical units, {row.get('available')} available; recorded MYR {row.get('recorded_gross')} gross, {row.get('recorded_refunds')} refunds and {row.get('recorded_net')} net for inclusive {filters.get('start')} to {filters.get('end')}.",row['branch_id'])
        elif name=='inventory_risks':
            explanation=risk_answer([item],branches)
            findings.extend(explanation['findings'][:12])
            limitations.extend(explanation['limitations'])
        elif name=='forecast_evidence':
            for row in rows:
                if (row.get('qualification')=='provisional_synthetic' and row.get('freshness')=='fresh'
                        and row.get('status')=='provisional' and row.get('predicted_demand') is not None):
                    add(item,f"SKU {row['sku_id']}: {row['predicted_demand']} forecast units over {row['horizon_days']} days, origin {row.get('as_of')}; provisional synthetic evidence, not a production-qualified prediction or confidence score.",row['branch_id'])
                else:
                    add(item,f"SKU {row['sku_id']}: Inputs needed; forecast withheld because qualification, freshness or numerical evidence is unavailable.",row['branch_id'])
        elif name=='recommendation_preview':
            from services.planning_presentation import missing_inputs
            for row in rows[:9]:
                reasons=row.get('reason_codes') or []
                label=row.get('sku_code') or 'SKU '+str(row['sku_id'])
                if reasons:
                    add(item,label+': Inputs needed. '+' '.join(missing_inputs(reasons)),row['branch_id'])
                else:
                    quantities=[x for x in row['recommendations'] if x['type']=='replenishment']
                    quantity=quantities[0]['suggested_quantity'] if quantities else None
                    if quantity is None:
                        add(item,label+': Inputs needed; no supported order quantity is available.',row['branch_id'])
                    elif (row.get('advisory_only') and row.get('production_numeric_enabled') is False
                            and row.get('forecast',{}).get('qualification')=='provisional_synthetic'
                            and row.get('forecast',{}).get('freshness')=='fresh'):
                        add(item,f'{label}: provisional synthetic replenishment preview {quantity} units; human review required. This does not execute an order or stock change.',row['branch_id'])
                    else:
                        complete=False
                        add(item,label+': Inputs needed; unsupported recommendation qualification. No order quantity is presented.',row['branch_id'])
        elif name=='product_performance':
            for row in rows[:5]:
                units='Unknown' if row['units'] is None else row['units']
                add(item,f"SKU {row['sku_id']}: {units} observed synthetic units across {row['cells']} cells, {row['missing']} missing. Missing coverage is unknown, not zero demand; this is not recorded MYR revenue.",row['branch_id'])
        elif name=='report_artifact':
            add(item,'A bounded, owned, expiring artifact is attached as evidence; it is not a complete export.')
        if not rows and name not in ('sales_summary','inventory_summary','compare_branches'):
            add(item,'No matching rows in this result; this does not prove zero demand or absence outside the queried scope.')
        if name=='inventory_summary' and not rows:
            add(item,'No lot matched the requested filter. Full-scope totals above do not describe the requested product.')
    if conflict:
        findings=[];complete=False
        limitations.append('Source summaries disagree for the same scope and dates. A combined conclusion is withheld; refresh and reconcile the evidence.')
    limitations.append('Each citation retains its own source, as-of time and authorized scope. Sources are read separately, so figures can change between reads. No stock or orders were changed.')
    return dict(kind='deterministic_local_evidence',title='Verified business evidence',
        summary='The authorized evidence supports the findings below.' if findings else 'Insufficient consistent evidence for a factual conclusion.',
        findings=findings,coverage_complete=complete,
        covered_rows=0 if conflict else verified_rows,
        ranked=False,limitations=list(dict.fromkeys(limitations)),
        disclosure='Prepared locally from records available to your account; not a Gemini-written answer or production forecasting advice.')
