"""Presentation of authorized planning facts; no forecasts, writes, or new policy."""

BRANCH_NAMES = {'Demo Central': 'Central', 'Demo East': 'Eastside', 'Demo West': 'Westside',
                'DEMO-01': 'Central', 'DEMO-02': 'Eastside', 'DEMO-03': 'Westside'}


def branch_name(value):
    return BRANCH_NAMES.get(str(value), str(value))


def missing_inputs(reasons):
    messages = []
    for reason in reasons:
        if reason=='POLICY_VISIBILITY_RESTRICTED':
            text = 'Supplier planning is restricted for your role; ask headquarters to review it.'
        elif reason.startswith('FORECAST_'):
            text = 'A current demo forecast with the required source and date checks is needed to estimate an order.'
        elif 'POLICY' in reason and 'EXPIRY' not in reason:
            text = 'Review supplier delivery time, minimum order, pack size and the extra stock buffer.'
        elif 'EXPIRY' in reason:
            text = 'An expiry policy is needed to avoid planning unusable stock.'
        elif 'LOT' in reason or 'STOCK' in reason or 'OPENING' in reason:
            text = 'Verify lot balances and expiry labels before planning stock.'
        elif 'INBOUND' in reason or reason.startswith(('PO_', 'TRANSFER_')):
            text = 'Confirm incoming quantities and arrival dates to avoid duplicate supply.'
        elif reason == 'SYNTHETIC_REVIEW_ENVIRONMENT_REQUIRED':
            text = 'Order quantities are shown only in the separate planning demo with reviewed forecast and supplier inputs.'
        elif reason == 'SUPPLIER_NOT_ACTIVE':
            text = 'Choose an active supplier before preparing an order.'
        elif reason == 'SKU_NOT_ACTIVE_OR_OFFERED':
            text = 'Confirm this product is approved for the branch.'
        else:
            text = 'Review the planning evidence before choosing a quantity.'
        if text not in messages:
            messages.append(text)
    return messages


def review_cards(report, labels):
    """One example per visible branch before additional examples; not a ranking."""
    rows = report['items']
    chosen, seen = [], set()
    for row in rows:
        if row['branch_id'] not in seen:
            chosen.append(row)
            seen.add(row['branch_id'])
    chosen += [row for row in rows if row not in chosen]
    cards = []
    for row in chosen[:3]:
        calculation = row['calculation']
        quantity = calculation['purchase_quantity'] if calculation is not None else None
        reasons = missing_inputs(row['reason_codes'])
        cards.append(dict(decision_id=row.get('decision_id',''),branch_id=row['branch_id'], branch=labels.get(str(row['branch_id']), branch_name(row['branch_code'])),
            sku_id=row['sku_id'], sku=row['sku_code'], available=row['inputs']['available_stock'],
            quantity=quantity, excess=calculation['potential_excess'] if calculation else None,
            demand=calculation['demand'] if calculation else None,
            title='Review replenishment' if quantity else 'Review stock position' if calculation else 'Complete planning inputs',
            reasons=reasons, ready=calculation is not None))
    return cards
